import asyncio
import discord
import json
import logging
import requests
import typing
import yaml

from types import SimpleNamespace
from functools import wraps, partial


def to_thread(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # loop = asyncio.get_event_loop()
        callback = partial(func, *args, **kwargs)
        return await asyncio.to_thread(callback)  # if using python 3.9+ use `await asyncio.to_thread(callback)`
    return wrapper


class App(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.logger = logging.getLogger('discord')

        with open('config.yml', 'r') as f:
            self.config = SimpleNamespace(**yaml.safe_load(f))

        self.history = {}


    async def on_ready(self) -> None:
        self.logger.info(f'Logged in as {self.user}')


    @to_thread
    def ollama(self, message: discord.Message) -> str:
        data = {
            'stream': False,
            'model': self.config.model,
            'options': {
                'num_ctx': 4096,
                # 'temperature': 2.0,
                # 'num_predict': -2
            }
        }

        prompt = {
            'role': 'user',
            'content': message.content
        }

        id = message.author.id

        if id in self.history:
            if len(self.history[id]) > self.config.history:
                self.history[id].pop(0)
            self.history[id].append(prompt)
        else:
            self.history[id] = [prompt]

        data['messages'] = self.history[id]

        self.logger.info(f'Sending: {self.history}')

        headers = { 'Content-Type': 'application/json' }
        req = requests.post(
            url=self.config.ollama + '/api/chat',
            headers=headers,
            data=json.dumps(data)
        )

        if req.status_code == 200:
            res = json.loads(req.text)
            self.logger.info(res)
            self.history[id].append(res['message'])
            self.logger.info(f'Storing: {self.history}')
            return res['message']['content']
        else:
            return f'Error talking to Ollama: [{req.status_code}] {req.text}'


    async def on_message(self, message: discord.Message) -> None:
        id = message.author.id
        args = message.content.split()

        # Is in a DM?
        is_dm = isinstance(message.channel, discord.channel.DMChannel)

        # Ignore other bots and empty messages
        if message.author.bot or not args:
            return

        # Ignore channels not in whitelist, allow DMs
        if not message.channel.id in self.config.channels and not is_dm:
            return

        self.logger.info(f'User: {message.author}, Msg: {message.content}')

        command = args[0].lower()

        if is_dm:
            channel = await self.create_dm(message.author)
            reference = None
        else:
            channel = message.channel
            reference = message

        match command:
            case '.reset':
                if id in self.history:
                    del self.history[id]
                await channel.send('What were we talking about again?', reference=reference)
            case _:
                async with channel.typing():
                    res = await self.ollama(message)
                    await channel.send(res, reference=reference)


def main() -> None:
    bot = App()
    bot.run(bot.config.token)

if __name__ == "__main__":
    main()
