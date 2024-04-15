import asyncio
import discord
import logging
import ollama
import os
import textwrap

from collections import deque
from dotenv import load_dotenv


class SharedHistory:
    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self.history = deque(maxlen=self.max_size)

    def add(self, message: dict) -> None:
        self.history.append(message)

    def get(self) -> list:
        return list(self.history)

    def clear(self) -> None:
        self.history.clear()


class Config:
    def __init__(self) -> None:
        load_dotenv()
        self.token: str = os.getenv("DISCORD_TOKEN", "")
        self.channels: list[str] = os.getenv("DISCORD_CHANNELS", "").split(",")
        self.url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model: str = os.getenv("OLLAMA_MODEL", "llama2")
        self.max_history: int = int(os.getenv("OLLAMA_HISTORY", 20))
        self.system: str = os.getenv("OLLAMA_SYSTEM", "You are a helpful assistant")


class App(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = Config()
        self.logger = logging.getLogger("discord")
        self.ol = ollama.AsyncClient(self.config.url)
        self.history = SharedHistory(self.config.max_history)
        self.lock = asyncio.Lock()
        self.run(token=self.config.token)

    async def on_ready(self) -> None:
        self.logger.info(self.config.channels)
        self.logger.info(f"Logged in as {self.user}")
        self.logger.info(f"Initializing LlamaCord model from {self.config.model}...")
        try:
            await self.ol.create(
                model="llamacord",
                modelfile=f'FROM {self.config.model}\nSYSTEM """{self.config.system}"""',
                stream=False,
            )
            self.logger.info("Loaded!")
        except Exception as e:
            self.logger.error(f"Error loading model: {e}")

    async def ollama(self, text: str) -> str:
        prompt = {'role': 'user', 'content': text}

        try:
            async with self.lock:
                self.history.add(prompt)
                messages = self.history.get()
                response = await self.ol.chat(
                    model="llamacord", messages=messages, stream=False, keep_alive=-1
                )
                self.history.add(response["message"])
                return response["message"]["content"]
        except Exception as e:
            return f"Error talking to Ollama: {e}"

    def split_response(self, text: str, max_length: int = 2000) -> list[str]:
        return textwrap.wrap(text, width=max_length, replace_whitespace=False)

    async def on_message(self, message: discord.Message) -> None:
        if not self.user:
            return
        args = filter(
            lambda s: s.strip() and not s.startswith("@"), message.clean_content.split()
        )
        is_mention = self.user.mentioned_in(message)
        is_reply = (
            message.type == discord.MessageType.reply
        ) and message.reference == self.user
        is_dm = isinstance(message.channel, discord.channel.DMChannel)
        is_allowed = (str(message.channel.id) in self.config.channels) or is_dm

        # Check for bots and whitelist
        if message.author.bot or not is_allowed:
            return

        text = " ".join(args)

        if is_dm:
            channel = await self.create_dm(message.author)
            reference = None
        elif is_mention or is_reply:
            channel = message.channel
            reference = message
        else:
            channel = message.channel
            reference = None

        if text.startswith('!reset'):
            async with self.lock:
                self.history.clear()
                await channel.send('History cleared!', reference=reference)
                return
        elif text.startswith('!history'):
            async with self.lock:
                history = self.history.get()
                output = '\n'.join([f'{item["role"]}: {item["content"]}' for item in history])
                for chunk in self.split_response(output):
                    await channel.send(chunk, reference=reference)
                return

        self.logger.info(f"User: {message.author}, Msg: {text}")

        async with channel.typing():
            res = await self.ollama(f"[{message.author.display_name}] {text}")
            for chunk in self.split_response(res):
                await channel.send(chunk, reference=reference)


def main() -> None:
    App()
