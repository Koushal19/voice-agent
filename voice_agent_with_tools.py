# --- Step 1: Environment Setup and Imports ---
# First, we need to import the necessary modules. We use `dotenv` to load our API keys
# (like `LIVEKIT_API_KEY`, `OPENAI_API_KEY`, etc.) from a local `.env` file. We also 
# import the core classes from `livekit.agents`, including the `AgentServer`, and 
# specific plugins for noise cancellation.


import logging
import asyncio
import httpx
from typing import Any 
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    RoomInputOptions,
    cli,
    TurnHandlingOptions
)

from livekit.agents import llm, stt, inference, tts
from livekit.agents.llm import function_tool, ToolError
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# Load environment variables containing API keys
load_dotenv()


# --- Step 2: Defining the Assistant Agent ---
# Next, we define our core logic by subclassing the `Agent` class. In the `__init__` 
# method, we provide the system instructions that dictate the assistant's persona 
# and behavior.
class Assistant(Agent):
    """
    The Assistant class defines the core personality and conversational behavior of the voice AI.

    By passing specific instructions (System Prompt) to the parent Agent class, 
    we shape how the LLM interprets queries and formats its responses to act as 
    a specialized persona—in this case, a Level 2 IT Support Agent.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions = "You are CAIRA, an experienced Level 2 IT Support Agent for an enterprise service desk. "
                "You are patient, technical but easy to understand, and focused on resolving the user's IT issues efficiently. "
                "Ask clarifying questions if needed, like device type or error codes. "
                "Keep your responses concise, conversational, and natural to speak out loud. "
                "Do not use emojis, markdown formatting, or long lists."
        )

    @function_tool
    async def lookup_weather(self, context: RunContext, location: str) -> dict[str, Any]:
        """Look up current weather information for the given location."""
        logging.info(f"Tool called: looking up weather for {location}")

        # Explicit business logic error handling
        if location.lower() == "mars":
            raise ToolError("This location is soon coming. Please join our mailing list to stay updated.")

        async with httpx.AsyncClient() as client:
            try:
                # 1. Geocoding: Find coordinates for the location
                geo_response = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": location, "count": 1},
                )
                geo_data = geo_response.json()

                if not geo_data.get("results"):
                    raise ToolError(f"Could not find location: {location}")

                lat = geo_data["results"][0]["latitude"]
                lon = geo_data["results"][0]["longitude"]
                place_name = geo_data["results"][0]["name"]

                # 2. Get current weather for those coordinates
                weather_response = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,weather_code",
                        "temperature_unit": "fahrenheit",
                    },
                )
                weather = weather_response.json()

                return {
                    "location": place_name,
                    "temperature_f": weather["current"]["temperature_2m"],
                    "conditions": weather["current"]["weather_code"],
                }
            except httpx.HTTPError as e:
                logging.error(f"HTTP error fetching weather: {e}")
                raise ToolError("The weather service is temporarily unavailable.")
            except Exception as e:
                logging.error(f"Unexpected error in lookup_weather: {e}")
                raise ToolError("An unexpected error occurred while looking up the weather.")
    @function_tool
    async def check_server_status(self, context: RunContext, service_name: str) -> str:
        """Check the current status of a specific corporate server or service."""
        logging.info(f"Tool called: checking status for {service_name}")
        
        if "email" in service_name.lower() or "exchange" in service_name.lower():
            return f"The {service_name} is currently down for maintenance. Estimated recovery: 20 minutes."
        
        return f"The {service_name} is fully operational and reporting no issues."

    @function_tool
    async def reset_password(self, context: RunContext, employee_username: str) -> str:
        """Trigger a password reset link to be sent to an employee's secondary email."""
        logging.info(f"Tool called: resetting password for {employee_username}")
        return f"Done! A secure password reset link has been sent to the recovery email for {employee_username}."

    @function_tool
    async def process_payment(self, context: RunContext, amount: float, account_id: str) -> str:
        """Process a payment for a specific account."""
        
        # Prevent the tool from being cancelled if the user speaks
        context.disallow_interruptions() 
        
        logging.info(f"Tool called: processing payment of ${amount} for account {account_id}. Simulating network delay...")
        
        # Simulate a slow external API call (e.g., reaching out to Stripe or PayPal)
        await asyncio.sleep(5)
        
        return f"Successfully processed the payment of ${amount} for account {account_id}."

    @function_tool()
    async def search_knowledge_base(self, context: RunContext, query: str) -> str:
        """Search the internal IT knowledge base for troubleshooting articles."""
        
        # Send a verbal status update to the user after a short delay
        async def _speak_status_update(delay: float = 0.5):
            await asyncio.sleep(delay)
            await context.session.generate_reply(instructions=f"""
                You are searching the knowledge base for "{query}" but it is taking a little while.
                Update the user on your progress, but be very brief.
            """)
        
        status_update_task = asyncio.create_task(_speak_status_update(0.5))

        logging.info(f"Tool called: searching for '{query}'...")
        
        # Simulate a variable-length search operation
        await asyncio.sleep(8) 
        result = f"Found relevant articles for '{query}'. Suggest rebooting the affected system."
        
        # Cancel status update if search completed before the timeout
        status_update_task.cancel()
        
        return result


# --- Step 3: Configuring the Agent Server ---
# Now we instantiate our `AgentServer`. This object is responsible for managing 
# the incoming agent workers and their sessions.

server = AgentServer()

# --- Step 4: Configuring the Agent Session ---
# Next, we define the `entrypoint` function and decorate it with `@server.rtc_session()`, 
# which registers this function to handle incoming RTC sessions (like a user joining a room).
#
# Notice the `ctx: JobContext` parameter. The `JobContext` provides essential information 
# about the current job your agent is handling. It grants your agent access to the specific 
# LiveKit `Room` object it is connecting to, details about the participants, and methods 
# to accept or manage the connection.
#
# Inside the entrypoint, we instantiate an `AgentSession` and configure the pipeline 
# of AI models that will process the audio. We specify the models for STT, LLM, and TTS.

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    """
    The entrypoint is triggered when the worker connects to a LiveKit room.
    """

    # 1. Define STT Fallback (AssemblyAI -> Deepgram)
    resilient_stt = stt.FallbackAdapter(
        [
            inference.STT.from_model_string("assemblyai/universal-streaming:en"),
            inference.STT.from_model_string("deepgram/nova-3"),
        ]
    )

    # 2. Define LLM Fallback (OpenAI -> Google Gemini)
    resilient_llm = llm.FallbackAdapter(
        [
            inference.LLM(model="openai/gpt-4.1-mini"),
            inference.LLM(model="google/gemini-2.5-flash"),
        ]
    )

    # 3. Define TTS Fallback (Cartesia -> Inworld)
    resilient_tts = tts.FallbackAdapter(
        [
            inference.TTS.from_model_string(
                "cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
            ),
            inference.TTS.from_model_string("inworld/inworld-tts-1"),
        ]
    )
    
    # 4. Inject the resilient adapters and Turn Detection into the AgentSession
    session = AgentSession(
        stt=resilient_stt,
        llm=resilient_llm,
        tts=resilient_tts,
        vad=silero.VAD.load(),
        # Configuring Semantic Turn Detection to wait for natural sentence completion before interrupting
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
        ),
    )
   
    # --- Step 5: Starting the Session and Connecting ---
    # Once the session is configured, we need to start it by passing in our `Assistant` 
    # instance, the LiveKit room object, and any input options.
    #
    # To ensure the AI doesn't get confused by background noise or its own voice echoing, 
    # we apply Background Voice Cancellation (BVC) using the `noise_cancellation` plugin. 
    # Finally, we establish the connection to the room.

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC()
    )
    )

    await ctx.connect()

if __name__ == "__main__":
    # --- Step 6: Running the Agent Server ---
    # Finally, we run the agent server. The `cli.run()` method starts the server and 
    # listens for incoming connections. It also provides a command-line interface (CLI) 
    # for managing the server.
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)

