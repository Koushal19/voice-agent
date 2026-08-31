# --- Step 1: Environment Setup and Imports ---
# First, we need to import the necessary modules. We use `dotenv` to load our API keys
# (like `LIVEKIT_API_KEY`, `OPENAI_API_KEY`, etc.) from a local `.env` file. We also 
# import the core classes from `livekit.agents`, including the `AgentServer`, and 
# specific plugins for noise cancellation.


import logging
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RoomInputOptions,
    cli,
    TurnHandlingOptions
)

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

    # 1. Configure the AI pipeline models
    session = AgentSession(
        stt="deepgram/nova-3",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detector=MultilingualModel
        )
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

