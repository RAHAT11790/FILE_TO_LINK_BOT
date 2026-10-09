import jinja2
from info import *
from TechVJ.bot import TechVJBot
from TechVJ.util.human_readable import humanbytes
from TechVJ.util.file_properties import get_file_ids
from TechVJ.server.exceptions import InvalidHash
import urllib.parse
import logging


async def render_page(id, secure_hash, src=None):
    file_data = await get_file_ids(TechVJBot, int(LOG_CHANNEL), int(id))

    unique_id = file_data.unique_id or ""
    if unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {unique_id[:6]}")
        logging.debug(f"Invalid hash for message with - ID {id}")
        raise InvalidHash

    # ফাইল নাম optional. থাকলে দেখাবে, না থাকলে খালি থাকবে।
    file_name = (file_data.file_name or "").replace("_", " ")

    # স্ট্রিমিং সবসময় LOG_CHANNEL এর message id + hash দিয়ে হয়।
    src = urllib.parse.urljoin(
        URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name or '')}?hash={secure_hash}",
    )

    tag = (file_data.mime_type or "").split("/")[0].strip()
    file_size = humanbytes(file_data.file_size)

    if tag in ["video", "audio"]:
        template_file = "TechVJ/template/req.html"
        with open(template_file) as f:
            template = jinja2.Template(f.read(), autoescape=True)
        return template.render(
            file_name=file_name,
            file_url=src,
            file_size=file_size,
            file_unique_id=unique_id,
        )

    # dl.html uses old %s string interpolation (not Jinja).
    template_file = "TechVJ/template/dl.html"
    with open(template_file) as f:
        page = f.read()
    return page % (file_name, file_name, src, file_size)
