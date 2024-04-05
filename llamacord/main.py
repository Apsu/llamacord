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
        callback = partial(func, *args, **kwargs)
        return await asyncio.to_thread(callback)
    return wrapper


class App(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.logger = logging.getLogger('discord')
        self.history = {}
        with open('config.yml', 'r') as f:
            self.config = SimpleNamespace(**yaml.safe_load(f))


    async def on_ready(self) -> None:
        self.logger.info(f'Logged in as {self.user}')


    @to_thread
    def ollama(self, id: int, text: str) -> str:
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
            'content': text
        }

        if id in self.history:
            if len(self.history[id]) > self.config.history:
                self.history[id].pop(0)
            self.history[id].append(prompt)
        else:
            self.history[id] = [prompt]

        data['messages'] = self.history[id]

        headers = { 'Content-Type': 'application/json' }
        req = requests.post(
            url=self.config.ollama + '/api/chat',
            headers=headers,
            data=json.dumps(data)
        )

        self.logger.info(f'Sending: {data}')

        if req.status_code == 200:
            res = json.loads(req.text)
            self.history[id].append(res['message'])
            # self.logger.info(f'Storing: {self.history[id]}')
            return res['message']['content']
        else:
            return f'Error talking to Ollama: [{req.status_code}] {req.text}'

    def split_response(self, text: str) -> list[str]:
        # Discord response limit
        max = 2000
        res = []
        lines = text.splitlines()        
        last = lines.pop(0)
        for line in lines:
            if not line.strip():
                last += '\n'
                continue
            
            cur = '\n'.join([last, line])
            if len(cur) > max:
                res.append(last)
                last = line
            else:
                last = cur

        res.append(last)
        return res


    async def on_message(self, message: discord.Message) -> None:
        id = message.author.id
        args = [arg.strip() for arg in message.content.split() if arg.strip()]

        is_dm = isinstance(message.channel, discord.channel.DMChannel)
        is_mention = self.user in message.mentions
        is_reply = (message.type == discord.MessageType.reply) and is_mention
        is_allowed = (message.channel.id in self.config.channels) or is_dm

        # self.logger.info(f'DM: {is_dm}, Mention: {is_mention}, Reply: {is_reply}, Allowed: {is_allowed}')

        # Check for bots, whitelist, and empty messages
        if message.author.bot or not is_allowed or not args:
            return

        # Check for DMs, mentions, and replies
        if not is_dm and not is_mention and not is_reply:
            return
        
        # Strip mentions out
        args = [s for s in args if not any(str(user.id) in s for user in message.mentions)]
        command = args[0].lower()
        text = " ".join(args)
        
        if is_dm:
            channel = await self.create_dm(message.author)
            reference = None
        else:
            channel = message.channel
            reference = message

        self.logger.info(f'User: {message.author}, Cmd: {command}, Msg: {text}')

        match command:
            case '.reset':
                if id in self.history:
                    del self.history[id]
                async with channel.typing():
                    await channel.send('What were we talking about again?', reference=reference)
            case _:
                async with channel.typing():
                    res = await self.ollama(id, text)
                    for chunk in self.split_response(res):
                        await channel.send(chunk, reference=reference)


def main() -> None:
    bot = App()
    bot.run(bot.config.token)

if __name__ == "__main__":
    main()
