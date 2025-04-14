"""Utilities"""

import functools
import mimetypes
import os
import re
import time
from enum import Enum
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Generator, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup
from deltachat import Chat, Message
from html2text import html2text
from mastodon import (
    AttribAccessDict,
    Mastodon,
    MastodonNetworkError,
    MastodonServerError,
    MastodonUnauthorizedError,
)
from pydub import AudioSegment
from simplebot.bot import DeltaBot, Replies

from .orm import Account, Client, DmChat, session_scope

SPAM = [
    "/fediversechick/",
    "https://discord.gg/83CnebyzXh",
    "https://matrix.to/#/#nicoles_place:matrix.org",
]
MUTED_NOTIFICATIONS = ("reblog", "favourite", "follow")
TOOT_SEP = "\n\n―――――――――――――――\n\n"
STRFORMAT = "%Y-%m-%d %H:%M"
_scope = __name__.split(".", maxsplit=1)[0]
web = requests.Session()
web.request = functools.partial(web.request, timeout=10)  # type: ignore


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


def toots2texts(bot: DeltaBot, toots: Iterable) -> Generator:
    prefix = getdefault(bot, "cmd_prefix", "")
    for toot in toots:
        reply = toot2reply(prefix, toot)
        text = reply.get("text", "")
        if reply.get("filename"):
            if not text.startswith("http"):
                text = "\n" + text
            text = reply["filename"] + "\n" + text
        sender = reply.get("sender", "")
        if sender:
            text = f"{sender}:\n{text}"
        if text:
            yield text


def toots2replies(bot: DeltaBot, toots: Iterable) -> Generator:
    prefix = getdefault(bot, "cmd_prefix", "")
    for toot in toots:
        reply = toot2reply(prefix, toot)
        if reply.get("filename"):
            try:
                reply["filename"] = download_file(bot, reply["filename"])
            except Exception as ex:
                bot.logger.exception(ex)
                text = reply.get("text", "")
                if not text.startswith("http"):
                    text = "\n" + text
                reply["text"] = reply["filename"] + "\n" + text
                del reply["filename"]
        if reply:
            yield reply


def toot2reply(prefix: str, toot: AttribAccessDict) -> dict:
    text = ""
    reply = {}
    if toot.reblog:
        reply["sender"] = _get_name(toot.reblog.account)
        text += f"🔁 {_get_name(toot.account)}\n\n"
        toot = toot.reblog
    else:
        reply["sender"] = _get_name(toot.account)

    if toot.media_attachments:
        reply["filename"] = toot.media_attachments.pop(0).url
        text += "\n".join(media.url for media in toot.media_attachments) + "\n\n"

    soup = BeautifulSoup(toot.content, "html.parser")
    if toot.mentions:
        accts = {e.url: "@" + e.acct for e in toot.mentions}
        for anchor in soup("a", class_="u-url"):
            name = accts.get(anchor["href"], "")
            if name:
                anchor.string = name
    for linebreak in soup("br"):
        linebreak.replace_with("\n")
    for paragraph in soup("p"):
        paragraph.replace_with(paragraph.get_text() + "\n\n")
    text += soup.get_text()

    text += f"\n\n[{v2emoji[toot.visibility]} {toot.created_at.strftime(STRFORMAT)}]({toot.url})\n"
    text += f"↩️ /{prefix}reply_{toot.id}\n"
    text += f"⭐ /{prefix}star_{toot.id}\n"
    if toot.visibility in (Visibility.PUBLIC, Visibility.UNLISTED):
        text += f"🔁 /{prefix}boost_{toot.id}\n"
    text += f"⏫ /{prefix}open_{toot.id}\n"
    text += f"👤 /{prefix}profile_{toot.account.id}\n"

    reply["text"] = text
    return reply


def notif2replies(toots: Iterable) -> Generator:
    for toot in toots:
        reply = notif2reply(toot)
        if reply:
            yield reply


def notif2reply(toots: list[AttribAccessDict]) -> dict:
    toot_type = toots[0].type
    name = ", ".join(_get_name(t.account) for t in toots)

    if toot_type == "follow":
        return {"text": f"👤 {name} followed you."}

    if toot_type == "reblog":
        text = f"🔁 {name} boosted your toot."
    elif toot_type == "favourite":
        text = f"⭐ {name} favorited your toot."
    else:  # unsupported type
        assert toot_type != "mention"  # mentions are handled with toots2replies
        return {}

    toot = toots[0].status
    text += f"\n\n[{v2emoji[toot.visibility]} {toot.created_at.strftime(STRFORMAT)}]({toot.url})"
    return {"text": text, "html": toot.content}


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


def download_file(bot: DeltaBot, url: str, default_extension="") -> str:
    """Download a file and save the file in the bot's blobs folder."""
    with web.get(url) as resp:
        ext = get_extension(resp) or default_extension
        with NamedTemporaryFile(
            dir=bot.account.get_blobdir(), suffix=ext, delete=False
        ) as temp_file:
            path = temp_file.name
        with open(path, "wb") as file:
            file.write(resp.content)
    return path


def normalize_url(url: str) -> str:
    if url.startswith("http://"):
        url = "https://" + url[4:]
    elif not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")


def getdefault(bot: DeltaBot, key: str, value: Optional[str] = None) -> str:
    val = bot.get(key, scope=_scope)
    if val is None and value is not None:
        bot.set(key, value, scope=_scope)
        val = value
    return val


def get_database_path(bot: DeltaBot) -> str:
    path = os.path.join(os.path.dirname(bot.account.db_path), _scope)
    if not os.path.exists(path):
        os.makedirs(path)
    return os.path.join(path, "sqlite.db")


def get_profile(bot: DeltaBot, masto: Mastodon, username: Optional[str] = None) -> str:
    me = masto.me()
    if not username:
        user = me
    else:
        user = get_user(masto, username)
        if user is None:
            return "❌ Invalid user"

    text = f"{_get_name(user)}:\n\n"
    fields = ""
    for f in user.fields:
        fields += f"{html2text(f.name).strip()}: {html2text(f.value).strip()}\n"
    if fields:
        text += fields + "\n\n"
    text += html2text(user.note).strip()
    text += f"\n\nToots: {user.statuses_count}\nFollowing: {user.following_count}\nFollowers: {user.followers_count}"
    if user.id != me.id:
        rel = masto.account_relationships(user)[0]
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
    text += TOOT_SEP.join(toots2texts(bot, reversed(toots)))
    return text


def listen_to_mastodon(bot: DeltaBot) -> None:
    while True:
        try:
            _check_mastodon(bot)
        except Exception as ex:
            bot.logger.exception(ex)


def _check_mastodon(bot: DeltaBot) -> None:
    while True:
        bot.logger.debug("Checking Mastodon")
        instances: dict = {}
        with session_scope() as session:
            acc_count = session.query(Account).count()
            bot.logger.debug(f"Accounts to check: {acc_count}")
            for acc in session.query(Account):
                instances.setdefault(acc.url, []).append(
                    (
                        acc.addr,
                        acc.token,
                        acc.home,
                        acc.last_home,
                        acc.muted_home,
                        acc.notifications,
                        acc.last_notif,
                        acc.muted_notif,
                    )
                )

        start_time = time.time()
        instances_count = len(instances)
        total_acc = acc_count
        while instances:
            bot.logger.debug(
                f"Check: {acc_count} accounts across {instances_count} instances remaining..."
            )
            for key in list(instances.keys()):
                (
                    addr,
                    token,
                    home_chat,
                    last_home,
                    muted_home,
                    notif_chat,
                    last_notif,
                    muted_notif,
                ) = instances[key].pop()
                acc_count -= 1
                if not instances[key]:
                    instances.pop(key)
                    instances_count -= 1
                bot.logger.debug(f"{addr}: Checking account ({key})")
                try:
                    masto = get_mastodon(key, token)
                    _check_notifications(
                        bot, masto, addr, notif_chat, last_notif, muted_notif
                    )
                    if muted_home:
                        bot.logger.debug(f"{addr}: Ignoring Home timeline (muted)")
                    else:
                        _check_home(bot, masto, addr, home_chat, last_home)
                    bot.logger.debug(f"{addr}: Done checking account")
                except MastodonUnauthorizedError as ex:
                    bot.logger.exception(ex)
                    chats: List[int] = []
                    with session_scope() as session:
                        acc = session.query(Account).filter_by(addr=addr).first()
                        if acc:
                            chats.extend(dmchat.chat_id for dmchat in acc.dm_chats)
                            chats.append(acc.home)
                            chats.append(acc.notifications)
                            session.delete(acc)
                    for chat_id in chats:
                        try:
                            bot.get_chat(chat_id).remove_contact(bot.self_contact)
                        except ValueError:
                            pass

                    bot.get_chat(addr).send_text(
                        f"❌ ERROR Your account was logged out: {ex}"
                    )
                except (MastodonNetworkError, MastodonServerError) as ex:
                    bot.logger.exception(ex)
                except Exception as ex:  # noqa
                    bot.logger.exception(ex)
                    bot.get_chat(addr).send_text(
                        f"❌ ERROR while checking your account: {ex}"
                    )
            time.sleep(2)
        elapsed = int(time.time() - start_time)
        delay = max(int(getdefault(bot, "delay")) - elapsed, 10)
        bot.logger.info(
            f"Done checking {total_acc} accounts, sleeping for {delay} seconds..."
        )
        time.sleep(delay)


def send_toot(
    masto: Mastodon,
    text: Optional[str] = None,
    filename: Optional[str] = None,
    visibility: Optional[str] = None,
    in_reply_to: Optional[str] = None,
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
            client_name="DeltaChat Bridge",
            website="https://github.com/simplebot-org/simplebot_mastodon",
            redirect_uris="urn:ietf:wg:oauth:2.0:oob",
            api_base_url=api_url,
            session=web,
        )
    except Exception:  # noqa
        client_id, client_secret = None, None
    session.add(Client(url=api_url, id=client_id, secret=client_secret))
    return client_id, client_secret


def get_mastodon(api_url: str, token: Optional[str] = None, **kwargs) -> Mastodon:
    return Mastodon(
        access_token=token,
        api_base_url=api_url,
        ratelimit_method="throw",
        session=web,
        **kwargs,
    )


def get_mastodon_from_msg(message: Message) -> Optional[Mastodon]:
    api_url, token = "", ""
    with session_scope() as session:
        acc = get_account_from_msg(message, session)
        if acc:
            api_url, token = acc.url, acc.token
    return get_mastodon(api_url, token) if api_url else None


def get_account_from_msg(message: Message, session) -> Optional[Account]:
    acc = get_account_from_chat(message.chat, session)
    if not acc:
        addr = message.get_sender_contact().addr
        acc = session.query(Account).filter_by(addr=addr).first()
    return acc


def get_account_from_chat(chat: Chat, session) -> Optional[Account]:
    if chat.is_multiuser():
        acc = (
            session.query(Account)
            .filter((Account.home == chat.id) | (Account.notifications == chat.id))
            .first()
        )
        if not acc:
            dmchat = session.query(DmChat).filter_by(chat_id=chat.id).first()
            if dmchat:
                acc = dmchat.account
    else:
        acc = None
    return acc


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


def _handle_dms(dms: list, bot: DeltaBot, addr: str, notif_chat: int) -> None:
    def _get_chat_id(acct) -> int:
        with session_scope() as session:
            dmchat = (
                session.query(DmChat).filter_by(acc_addr=addr, contact=acct).first()
            )
            if dmchat:
                chat_id = dmchat.chat_id
            else:
                chat_id = 0
        return chat_id

    prefix = getdefault(bot, "cmd_prefix", "")
    chats: Dict[str, int] = {}
    replies = Replies(bot, bot.logger)
    for dm in reversed(dms):
        reply = toot2reply(prefix, dm)
        if reply.get("filename"):
            try:
                reply["filename"] = download_file(bot, reply["filename"])
            except Exception as ex:
                bot.logger.exception(ex)
                text = reply.get("text", "")
                if not text.startswith("http"):
                    text = "\n" + text
                reply["text"] = reply["filename"] + "\n" + text
        if not reply:
            continue

        acct = dm.account.acct
        chat_id = chats.get(acct, 0)
        if not chat_id:
            chat_id = chats[acct] = _get_chat_id(acct)

        if chat_id:
            chat = bot.get_chat(chat_id)
        else:
            chat = bot.create_group(acct, bot.get_chat(notif_chat).get_contacts())
            chats[acct] = chat.id
            with session_scope() as session:
                session.add(DmChat(chat_id=chat.id, contact=acct, acc_addr=addr))

            try:
                path = download_file(bot, dm.account.avatar_static, ".jpg")
                chat.set_profile_image(path)
            except ValueError as err:
                bot.logger.exception(err)
                os.remove(path)
            except Exception as err:
                bot.logger.exception(err)

        replies.add(**reply, chat=chat)
        replies.send_reply_messages()


def _check_notifications(
    bot: DeltaBot,
    masto: Mastodon,
    addr: str,
    notif_chat: int,
    last_id: str,
    muted_notif: bool,
) -> None:
    dms = []
    notifications = []
    bot.logger.debug(f"{addr}: Getting Notifications (last_id={last_id})")
    toots = masto.notifications(min_id=last_id, limit=100)
    if toots:
        with session_scope() as session:
            acc = session.query(Account).filter_by(addr=addr).first()
            acc.last_notif = last_id = toots[0].id
        for toot in toots:
            if (
                toot.type == "mention"
                and toot.status.visibility == Visibility.DIRECT
                and len(toot.status.mentions) == 1
            ):
                content = toot.status.content
                if not any(keyword in content for keyword in SPAM):
                    dms.append(toot.status)
            elif not muted_notif or toot.type not in MUTED_NOTIFICATIONS:
                notifications.append(toot)

    if dms:
        bot.logger.debug(f"{addr}: Direct Messages: {len(dms)} new entries")
        _handle_dms(dms, bot, addr, notif_chat)

    bot.logger.debug(
        f"{addr}: Notifications: {len(notifications)} new entries (last_id={last_id})"
    )
    if notifications:
        reblogs: dict[str, AttribAccessDict] = {}
        favs: dict[str, AttribAccessDict] = {}
        follows = []
        mentions = []
        for toot in reversed(notifications):
            if toot.type == "reblog":
                reblogs.setdefault(toot.status.id, []).append(toot)
            elif toot.type == "favourite":
                favs.setdefault(toot.status.id, []).append(toot)
            elif toot.type == "follow":
                follows.append(toot)
            else:
                mentions.append(toot.status)
        notifs = [*reblogs.values(), *favs.values()]
        if follows:
            notifs.append(follows)
        chat = bot.get_chat(notif_chat)
        replies = Replies(bot, bot.logger)
        for reply in notif2replies(notifs):
            replies.add(**reply, chat=chat)
            replies.send_reply_messages()
        for reply in toots2replies(bot, mentions):
            replies.add(**reply, chat=chat)
            replies.send_reply_messages()


def _check_home(
    bot: DeltaBot, masto: Mastodon, addr: str, home_chat: int, last_id: str
) -> None:
    me = masto.me()
    bot.logger.debug(f"{addr}: Getting Home timeline (last_id={last_id})")
    toots = masto.timeline_home(min_id=last_id, limit=100)
    if toots:
        with session_scope() as session:
            acc = session.query(Account).filter_by(addr=addr).first()
            acc.last_home = last_id = toots[0].id
        toots = [
            toot for toot in toots if me.id not in [acc.id for acc in toot.mentions]
        ]
    bot.logger.debug(f"{addr}: Home: {len(toots)} new entries (last_id={last_id})")
    if toots:
        chat = bot.get_chat(home_chat)
        replies = Replies(bot, bot.logger)
        for reply in toots2replies(bot, reversed(toots)):
            replies.add(**reply, chat=chat)
            replies.send_reply_messages()
