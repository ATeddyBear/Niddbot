from pathlib import Path
from aiohttp import ClientSession
import shutil

from .audio import Audio
from .manager import start_lavalink_server
from discord.ext import commands
from redbot.core.data_manager import cog_data_path
import redbot.core

LAVALINK_DOWNLOAD_URL = (
    "https://github.com/Cog-Creators/Red-DiscordBot/"
    "releases/download/{}/Lavalink.jar"
).format(redbot.core.__version__)

LAVALINK_DOWNLOAD_DIR = cog_data_path(raw_name="Audio")
LAVALINK_JAR_FILE = LAVALINK_DOWNLOAD_DIR / "Lavalink.jar"

APP_YML_FILE = LAVALINK_DOWNLOAD_DIR / "application.yml"
BUNDLED_APP_YML_FILE = Path(__file__).parent / "application.yml"


async def download_lavalink(session):
    with LAVALINK_JAR_FILE.open(mode='wb') as f:
        async with session.get(LAVALINK_DOWNLOAD_URL) as resp:
            while True:
                chunk = await resp.content.read(512)
                if not chunk:
                    break
                f.write(chunk)


async def maybe_download_lavalink(loop, cog):
    jar_exists = LAVALINK_JAR_FILE.exists()
    current_build = redbot.core.VersionInfo(*await cog.config.current_build())

    session = ClientSession(loop=loop)

    if not jar_exists or current_build < redbot.core.version_info:
        LAVALINK_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        await download_lavalink(session)
        await cog.config.current_build.set(redbot.core.version_info.to_json())

    session.close()

    shutil.copyfile(str(BUNDLED_APP_YML_FILE), str(APP_YML_FILE))


async def setup(bot: commands.Bot):
    cog = Audio(bot)
    if not await cog.config.use_external_lavalink():
        await maybe_download_lavalink(bot.loop, cog)
        await start_lavalink_server(bot.loop)

    bot.add_cog(cog)
    bot.loop.create_task(cog.init_config())
