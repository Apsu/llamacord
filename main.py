import discord
import logging
import ollama
import yaml

from types import SimpleNamespace


class History():
    def __init__(self, max) -> None:
        self.max = max
        self.history = {}

    def get(self, id) -> list:
        return self.history.get(id, [])

    def set(self, id, item) -> None:
        if id in self.history:
            if len(self.history[id]) > self.max:
                self.history[id].pop(0)
            self.history[id].append(item)
        else:
            self.history[id] = [item]

    def clear(self, id) -> None:
        if id in self.history:
            del self.history[id]


class App(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        with open('config.yml', 'r') as f:
            self.config = SimpleNamespace(**yaml.safe_load(f))

        self.logger = logging.getLogger('discord')
        self.ol = ollama.AsyncClient(self.config.url)
        self.history = History(self.config.history)


    async def on_ready(self) -> None:
        self.logger.info(f'Logged in as {self.user}')
        self.logger.info(f'Initializing LlamaCord model from {self.config.model}...')
        try:
            await self.ol.create(
                model='llamacord',
                modelfile=f'FROM {self.config.model}\nSYSTEM """{self.config.system}"""',
                stream=False
            )
            self.logger.info('Loaded!')
        except Exception as e:
            self.logger.error(f'Error loading model: {e}')


    async def ollama(self, id: int, text: str) -> str:
        prompt = {
            'role': 'user',
            'content': text
        }

        self.history.set(id, prompt)

        try:
            messages = self.history.get(id)
            self.logger.info(messages)
            response = await self.ol.chat(
                model='llamacord',
                messages=messages,
                stream=False,
                keep_alive=-1
            )
            self.logger.info(response)
            self.history.set(id, response['message'])
            return response['message']['content']
        except Exception as e:
            return f'Error talking to Ollama: {e}'


    def split_response(self, text: str) -> list[str]:
        # Discord response limit
        max_length = 2000
        lines = text.splitlines()
        chunks = []
        current = ''
        for line in lines:
            if len(current) + len(line) > max_length:
                chunks.append(current)
                current = ''
            current += line + '\n'

        if current:
            chunks.append(current)

        return chunks


    async def on_message(self, message: discord.Message) -> None:
        id = message.author.id
        args = filter(lambda s: s.strip() and not s.startswith('@'), message.clean_content.split())
        is_mention = self.user.mentioned_in(message)
        is_reply = (message.type == discord.MessageType.reply) and message.reference == self.user
        is_dm = isinstance(message.channel, discord.channel.DMChannel)
        is_allowed = (message.channel.id in self.config.channels) or is_dm

        # Check for bots and whitelist
        if message.author.bot or not is_allowed:
            return

        text = ' '.join(args)

        if text.startswith('!reset'):
            self.history.clear(id)
            await message.channel.send('History cleared!', reference=reference)
            return

        if is_dm:
            channel = await self.create_dm(message.author)
            reference = None
        elif is_mention or is_reply:
            channel = message.channel
            reference = message
        else:
            return

        self.logger.info(f'User: {message.author}, Msg: {text}')

        async with channel.typing():
            res = await self.ollama(id, text)
            for chunk in self.split_response(res):
                await channel.send(chunk, reference=reference)


def main() -> None:
    bot = App()
    bot.run(bot.config.token)

if __name__ == '__main__':
    main()
