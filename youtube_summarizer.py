import functools
import logging
from anthropic.types.beta.tools.tools_beta_message import ToolsBetaMessage
import yt_dlp

import requests
import sys
from dotenv import dotenv_values
import time
from anthropic import Anthropic, RateLimitError, InternalServerError

MAX_TOKENS = 4096
CONFIG = dotenv_values(".env")

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MODEL_NAMES = {
    "haiku": "claude-3-haiku-20240307",
    "sonnet": "claude-3-sonnet-20240229",
    "opus": "claude-3-opus-20240229",
}

CLAUDE_MODEL = MODEL_NAMES["haiku"]

SYSTEM_PROMPT = """
User: You are a research assistant who summarizes videos for professors looking to create educational content. Your goal is to provide an exhaustive summary of the video content, highlighting key points and concepts.

You should aim to provide a summary that is clear, accurate, and informative. Make sure to include any important details and proper nouns (company names, product names, etc) that may be relevant to the video's content.

You will be provided with a subtitles file, which you can use to generate the summary. You can use this information to help you understand the video content and create a summary that is both informative and engaging. Make sure to summarize the entire video, not just parts of it.

Use markdown styling, bolding proper nouns. Use multiple levels of headers and nested bullet-point format to break the video summary into a logical structure. If the video contains any lists, make sure to represent them as bullet points.

Before answering, make sure to write down your thoughts under a #scratchpad header.
"""


client = Anthropic(
    api_key=CONFIG["claude_key"],
)


def with_retries(max_wait_time=float("inf")):
    """
    A decorator that adds retry functionality to a function.

    Args:
      max_wait_time (float, optional): The maximum wait time in seconds between retries. Defaults to infinity.

    Returns:
      function: The decorated function.

    """

    def fibonacci_wait_times():
        """
        Generates Fibonacci numbers for wait times.

        Yields the Fibonacci numbers starting from 0 and 1, which can be used as wait times in a loop.
        The Fibonacci sequence is generated until max_wait_time is reached (default: infinity)

        Returns:
          int: The next Fibonacci number in the sequence.

        """
        a, b = (0, 1)
        while True:
            yield a
            if b <= max_wait_time:
                (a, b) = (b, a + b)

    def decorator_with_retries(func):
        """
        A function wrapper that handles rate limit and internal server errors with retry functionality.
        Not intended for direct use - use the top level function, with_retries.
        """

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """
            A function wrapper that handles rate limit and internal server errors with retry functionality.
            Not intended for direct use - use the top level function, with_retries.
            """
            sleep_time_generator = fibonacci_wait_times()
            while True:
                try:
                    return func(*args, *kwargs)
                except RateLimitError as e:
                    sleep_time = next(sleep_time_generator)
                    logger.debug(e)
                    logger.error(
                        f"{e.status_code} Rate Limit Error: {e.message} \nSleeping {sleep_time}s before retrying"
                    )
                    time.sleep(sleep_time)
                    continue
                except InternalServerError as e:
                    sleep_time = (
                        next(sleep_time_generator) + 30
                    )  # Let's chill a bit longer for 5XX errors
                    logger.debug(e)
                    logger.error(
                        f"{e.status_code} Internal Server Error: {e.message} \nSleeping {sleep_time}s before retrying"
                    )
                    time.sleep(sleep_time)
                    continue

        return wrapper

    return decorator_with_retries


@with_retries(max_wait_time=60)
def ask_claude(new_message, messages: list = []):
    """
    Sends a new message to Claude and returns the response.

    Args:
        new_message (str): The new message to send to Claude.
        messages (list, optional): List of previous messages. Defaults to an empty list.

    Returns:
        tuple: A tuple containing the response from Claude and the updated list of messages.
    """
    logger.info("Requesting summary from Claude")
    new_messages = messages + [new_message]
    response: ToolsBetaMessage = client.beta.tools.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=new_messages,
    )
    return response, new_messages


def download_captions(video_url):
    """
    Downloads the captions for a YouTube video.

    Args:
        video_url (str): The URL of the YouTube video.

    Returns:
        str: The downloaded captions as a string, or None if no English captions are available.
    """
    ydl = yt_dlp.YoutubeDL(
        {
            "writesubtitles": True,
            "subtitleslangs": ["en"],
            "writeautomaticsub": True,
            "logtostderr": True,
            "quiet": True,
            "logger": logger,
        }
    )
    logger.info(f"Downloading captions for {video_url}")
    res = ydl.extract_info(video_url, download=False)
    if res["requested_subtitles"] and res["requested_subtitles"]["en"]:
        logger.debug(res["requested_subtitles"]["en"]["url"])
        response = requests.get(res["requested_subtitles"]["en"]["url"], stream=True)
        logger.debug(response.text)
        return response.text
    else:
        logger.error("This YouTube video does not have any English captions")
        return None


def main(video_url):
    captions = download_captions(video_url=video_url)
    if not captions:
        return
    new_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": captions},
            {"type": "text", "text": "Can you summarize the video?"},
        ],
    }
    response, _ = ask_claude(new_message)

    print(response.content[0].text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        logger.error(
            "Error: Please provide a valid YouTube URL as a command line argument."
        )
        sys.exit(1)

    video_url: str = sys.argv[1]
    if not video_url.startswith("https://www.youtube.com/watch?v="):
        logger.error("Error: Invalid YouTube URL format.")
        sys.exit(1)

    main(video_url=video_url)
