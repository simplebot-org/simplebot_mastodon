"""Utilities"""

import functools
import mimetypes
import re
import time
from enum import Enum
from tempfile import NamedTemporaryFile
from typing import Any, Generator, Optional

import requests
from bs4 import BeautifulSoup
from deltachat import Message
from html2text import html2text
from mastodon import Mastodon, MastodonNetworkError
from pydub import AudioSegment
from simplebot.bot import DeltaBot

from .orm import Account, Client, DmChat, session_scope

TOOT_SEP = "\n\n―――――――――――――――\n\n"
STRFORMAT = "%Y-%m-%d %H:%M"
web = requests.Session()
web.request = functools.partial(web.request, timeout=15)  # type: ignore


class Visibility(str, Enum):
    DIRECT = "direct"  # visible only to mentioned users
    PRIVATE = "private"  # visible only to followers
    UNLISTED = "unlisted"  # public but not appear on the public timeline
    PUBLIC = "public"  # post will be public


v2emoji = {
    Visibility.DIRECT: "✉",
    Visibility.PRIVATE: "🔒",
    Visibility.UNLISTED: "🔓",
    Visibility.PUBLIC: "🌎",
}


def toots2text(bot: DeltaBot, toots: list, notifications: bool = False) -> Generator:
    prefix = getdefault(bot, "cmd_prefix", "")
    for t in reversed(toots):
        if notifications:
            is_mention = False
            timestamp = t.created_at.strftime(STRFORMAT)
            if t.type == "reblog":
                text = f"🔁 {_get_name(t.account)} boosted your toot. ({timestamp})\n\n"
            elif t.type == "favourite":
                text = (
                    f"⭐ {_get_name(t.account)} favorited your toot. ({timestamp})\n\n"
                )
            elif t.type == "follow":
                yield f"👤 {_get_name(t.account)} followed you. ({timestamp})"
                continue
            elif t.type == "mention":
                is_mention = True
                text = f"{_get_name(t.account)}:\n\n"
            else:
                continue
            t = t.status
        elif t.reblog:
            text = f"{_get_name(t.reblog.account)}:\n🔁 {_get_name(t.account)}\n\n"
            t = t.reblog
        else:
            text = f"{_get_name(t.account)}:\n\n"

        media_urls = "\n".join(media.url for media in t.media_attachments)
        if media_urls:
            text += media_urls + "\n\n"

        soup = BeautifulSoup(t.content, "html.parser")
        if t.mentions:
            accts = {e.url: "@" + e.acct for e in t.mentions}
            for a in soup("a", class_="u-url"):
                name = accts.get(a["href"])
                if name:
                    a.string = name
        for br in soup("br"):
            br.replace_with("\n")
        for p in soup("p"):
            p.replace_with(p.get_text() + "\n\n")
        text += soup.get_text()

        text += f"\n\n[{v2emoji[t.visibility]} {t.created_at.strftime(STRFORMAT)}]\n"
        if not notifications or is_mention:
            text += f"↩️ /{prefix}reply_{t.id}\n"
            text += f"⭐ /{prefix}star_{t.id}\n"
            if t.visibility in (Visibility.PUBLIC, Visibility.UNLISTED):
                text += f"🔁 /{prefix}boost_{t.id}\n"
            text += f"⏫ /{prefix}open_{t.id}\n"

        yield text


def get_extension(resp: requests.Response) -> str:
    disp = resp.headers.get("content-disposition")
    if disp is not None and re.findall("filename=(.+)", disp):
        fname = re.findall("filename=(.+)", disp)[0].strip('"')
    else:
        fname = resp.url.split("/")[-1].split("?")[0].split("#")[0]
    if "." in fname:
        ext = "." + fname.rsplit(".", maxsplit=1)[-1]
    else:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = mimetypes.guess_extension(ctype) or ""
    return ext


def get_user(m, user_id) -> Any:
    user = None
    if user_id.isdigit():
        user = m.account(user_id)
    else:
        user_id = user_id.lstrip("@").lower()
        ids = (user_id, user_id.split("@")[0])
        for a in m.account_search(user_id):
            if a.acct.lower() in ids:
                user = a
                break
    return user


def normalize_url(url: str) -> str:
    if url.startswith("http://"):
        url = "https://" + url[4:]
    elif not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")


def getdefault(bot: DeltaBot, key: str, value: str = None) -> str:
    val = bot.get(key, scope=__name__)
    if val is None and value is not None:
        bot.set(key, value, scope=__name__)
        val = value
    return val


def get_profile(bot: DeltaBot, masto: Mastodon, username: str = None) -> str:
    me = masto.me()
    if not username:
        user = me
    else:
        user = get_user(masto, username)
        if user is None:
            return "❌ Invalid user"

    rel = masto.account_relationships(user)[0] if user.id != me.id else None
    text = f"{_get_name(user)}:\n\n"
    fields = ""
    for f in user.fields:
        fields += f"{html2text(f.name).strip()}: {html2text(f.value).strip()}\n"
    if fields:
        text += fields + "\n\n"
    text += html2text(user.note).strip()
    text += f"\n\nToots: {user.statuses_count}\nFollowing: {user.following_count}\nFollowers: {user.followers_count}"
    if user.id != me.id:
        if rel["followed_by"]:
            text += "\n[follows you]"
        elif rel["blocked_by"]:
            text += "\n[blocked you]"
        text += "\n"
        if rel["following"] or rel["requested"]:
            action = "unfollow"
        else:
            action = "follow"
        prefix = getdefault(bot, "cmd_prefix", "")
        text += f"\n/{prefix}{action}_{user.id}"
        action = "unmute" if rel["muting"] else "mute"
        text += f"\n/{prefix}{action}_{user.id}"
        action = "unblock" if rel["blocking"] else "block"
        text += f"\n/{prefix}{action}_{user.id}"
        text += f"\n/{prefix}dm_{user.id}"
    text += TOOT_SEP
    toots = masto.account_statuses(user, limit=10)
    text += TOOT_SEP.join(toots2text(bot, toots))
    return text


def listen_to_mastodon(bot: DeltaBot) -> None:
    while True:
        bot.logger.debug("Checking Mastodon")
        instances: dict = {}
        with session_scope() as session:
            for acc in session.query(Account):
                instances.setdefault(acc.url, []).append(
                    (
                        acc.addr,
                        acc.token,
                        acc.home,
                        acc.last_home,
                        acc.notifications,
                        acc.last_notif,
                    )
                )
        while instances:
            for key in list(instances.keys()):
                if not instances[key]:
                    instances.pop(key)
                    continue
                addr, token, home_chat, last_home, notif_chat, last_notif = instances[
                    key
                ].pop()
                try:
                    masto = get_mastodon(key, token)
                    _check_notifications(bot, masto, addr, notif_chat, last_notif)
                    _check_home(bot, masto, addr, home_chat, last_home)
                except MastodonNetworkError as ex:
                    bot.logger.exception(ex)
                except Exception as ex:  # noqa
                    bot.logger.exception(ex)
                    bot.get_chat(addr).send_text(
                        f"❌ ERROR while checking your account: {ex}"
                    )
            time.sleep(2)
        time.sleep(int(getdefault(bot, "delay")))


def send_toot(
    masto: Mastodon,
    text: str = None,
    filename: str = None,
    visibility: str = None,
    in_reply_to: str = None,
) -> None:
    if filename:
        if filename.endswith(".aac"):
            aac_file = AudioSegment.from_file(filename, "aac")
            filename = filename[:-4] + ".mp3"
            aac_file.export(filename, format="mp3")
        media = [masto.media_post(filename).id]
        if in_reply_to:
            masto.status_reply(
                masto.status(in_reply_to), text, media_ids=media, visibility=visibility
            )
        else:
            masto.status_post(text, media_ids=media, visibility=visibility)
    elif text:
        if in_reply_to:
            masto.status_reply(masto.status(in_reply_to), text, visibility=visibility)
        else:
            masto.status_post(text, visibility=visibility)


def get_client(session, api_url) -> tuple:
    client = session.query(Client).filter_by(url=api_url).first()
    if client:
        return client.id, client.secret

    try:
        client_id, client_secret = Mastodon.create_app(
            "DeltaChat Bridge", api_base_url=api_url
        )
    except Exception:  # noqa
        client_id, client_secret = None, None
    session.add(Client(url=api_url, id=client_id, secret=client_secret))
    return client_id, client_secret


def get_mastodon(api_url: str, token: str = None) -> Mastodon:
    return Mastodon(
        access_token=token,
        api_base_url=api_url,
        ratelimit_method="throw",
        session=web,
    )


def get_mastodon_from_msg(message: Message) -> Optional[Mastodon]:
    addr = message.get_sender_contact().addr
    api_url, token = "", ""
    with session_scope() as session:
        acc = session.query(Account).filter_by(addr=addr).first()
        if acc:
            api_url, token = acc.url, acc.token

    return get_mastodon(api_url, token) if api_url else None


def account_action(action: str, payload: str, message: Message) -> str:
    if not payload:
        return "❌ Wrong usage"

    masto = get_mastodon_from_msg(message)
    if masto:
        if payload.isdigit():
            user_id = payload
        else:
            user_id = get_user(masto, payload)
            if user_id is None:
                return "❌ Invalid user"
        getattr(masto, action)(user_id)
        return ""
    return "❌ You are not logged in"


def _get_name(macc) -> str:
    isbot = "[BOT] " if macc.bot else ""
    if macc.display_name:
        return isbot + f"{macc.display_name} (@{macc.acct})"
    return isbot + macc.acct


def _handle_dms(dms: list, bot: DeltaBot, addr: str) -> None:
    prefix = getdefault(bot, "cmd_prefix", "")
    for dm in reversed(dms):
        text = ""
        media_urls = "\n".join(media.url for media in dm.media_attachments)
        if media_urls:
            text += media_urls + "\n\n"

        soup = BeautifulSoup(dm.content, "html.parser")
        accts = {e.url: "@" + e.acct for e in dm.mentions}
        for a in soup("a", class_="u-url"):
            name = accts.get(a["href"])
            if name:
                a.string = name
        for br in soup("br"):
            br.replace_with("\n")
        for p in soup("p"):
            p.replace_with(p.get_text() + "\n\n")
        text += soup.get_text()
        text += f"\n\n[{v2emoji[dm.visibility]} {dm.created_at.strftime(STRFORMAT)}]\n"
        text += f"⭐ /{prefix}star_{dm.id}\n"

        chat_id = 0
        with session_scope() as session:
            dmchat = (
                session.query(DmChat)
                .filter_by(acc_addr=addr, contact=dm.account.acct)
                .first()
            )
            if dmchat:
                chat_id = dmchat.chat_id

        if chat_id:
            bot.get_chat(chat_id).send_text(text)
        else:
            chat = bot.create_group(dm.account.acct, [addr])
            with session_scope() as session:
                session.add(
                    DmChat(chat_id=chat.id, contact=dm.account.acct, acc_addr=addr)
                )

            with web.get(dm.account.avatar_static) as resp:
                ext = get_extension(resp) or ".jpg"
                with NamedTemporaryFile(
                    dir=bot.account.get_blobdir(), suffix=ext, delete=False
                ) as file:
                    path = file.name
                with open(path, "wb") as file:
                    file.write(resp.content)
                try:
                    chat.set_profile_image(path)
                except ValueError as err:
                    bot.logger.exception(err)

            chat.send_text(text)


def _check_notifications(
    bot: DeltaBot, masto: Mastodon, addr: str, notif_chat: int, last_notif: str
) -> None:
    max_id = None
    dms = []
    notifications = []
    while True:
        ns = masto.notifications(max_id=max_id, since_id=last_notif)
        if not ns:
            break
        if max_id is None:
            with session_scope() as session:
                acc = session.query(Account).filter_by(addr=addr).first()
                acc.last_notif = ns[0].id
        max_id = ns[-1]
        for n in ns:
            if (
                n.type == "mention"
                and n.status.visibility == Visibility.DIRECT
                and len(n.status.mentions) == 1
            ):
                dms.append(n.status)
            else:
                notifications.append(n)

    if dms:
        _handle_dms(dms, bot, addr)

    bot.logger.debug(
        "Notifications: %s new entries (last id: %s)",
        len(notifications),
        last_notif,
    )
    if notifications:
        bot.get_chat(notif_chat).send_text(
            TOOT_SEP.join(toots2text(bot, notifications, True))
        )


def _check_home(
    bot: DeltaBot, masto: Mastodon, addr: str, home_chat: int, last_home: str
) -> None:
    me = masto.me()
    max_id = None
    toots: list = []
    while True:
        ts = masto.timeline_home(max_id=max_id, since_id=last_home)
        if not ts:
            break
        if max_id is None:
            with session_scope() as session:
                acc = session.query(Account).filter_by(addr=addr).first()
                acc.last_home = ts[0].id
        max_id = ts[-1]
        for t in ts:
            for a in t.mentions:
                if a.id == me.id:
                    break
            else:
                toots.append(t)
    bot.logger.debug("Home: %s new entries (last id: %s)", len(toots), last_home)
    if toots:
        bot.get_chat(home_chat).send_text(TOOT_SEP.join(toots2text(bot, toots)))