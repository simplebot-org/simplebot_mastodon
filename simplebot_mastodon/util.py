import os
import sqlite3
import time
from enum import Enum
from tempfile import NamedTemporaryFile
from typing import Any, Generator

import mastodon
import requests
from bs4 import BeautifulSoup
from pydub import AudioSegment
from simplebot.bot import DeltaBot, Replies

from .db import DBManager

TOOT_SEP = "\n\n―――――――――――――――\n\n"
STRFORMAT = "%Y-%m-%d %H:%M"


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


def toots2text(toots: list, acc_id: int, notifications: bool = False) -> Generator:
    for t in reversed(toots):
        if notifications:
            is_mention = False
            timestamp = t.created_at.strftime(STRFORMAT)
            if t.type == "reblog":
                text = f"🔁 {get_name(t.account)} boosted your toot. ({timestamp})\n\n"
            elif t.type == "favourite":
                text = f"⭐ {get_name(t.account)} favorited your toot. ({timestamp})\n\n"
            elif t.type == "follow":
                yield f"👤 {get_name(t.account)} followed you. ({timestamp})"
                continue
            elif t.type == "mention":
                is_mention = True
                text = f"{get_name(t.account)}:\n\n"
            else:
                continue
            t = t.status
        elif t.reblog:
            text = f"{get_name(t.reblog.account)}:\n🔁 {get_name(t.account)}\n\n"
            t = t.reblog
        else:
            text = f"{get_name(t.account)}:\n\n"

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
            text += f"↩️ /m_reply_{acc_id}_{t.id}\n"
            text += f"⭐ /m_star_{acc_id}_{t.id}\n"
            if t.visibility in (Visibility.PUBLIC, Visibility.UNLISTED):
                text += f"🔁 /m_boost_{acc_id}_{t.id}\n"
            text += f"⏫ /m_cntx_{acc_id}_{t.id}\n"

        yield text


def get_name(macc) -> str:
    isbot = "[BOT] " if macc.bot else ""
    if macc.display_name:
        return isbot + f"{macc.display_name} (@{macc.acct})"
    return isbot + macc.acct


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


def get_db(bot: DeltaBot) -> DBManager:
    path = os.path.join(os.path.dirname(bot.account.db_path), __name__)
    if not os.path.exists(path):
        os.makedirs(path)
    return DBManager(os.path.join(path, "sqlite.db"))


def rmprefix(text, prefix) -> str:
    return text[text.startswith(prefix) and len(prefix) :]


def logout(db: DBManager, bot: DeltaBot, acc, replies: Replies) -> None:
    me = bot.self_contact
    for pchat in db.get_pchats(acc["id"]):
        g = bot.get_chat(pchat["id"])
        try:
            g.remove_contact(me)
        except ValueError:
            pass
    try:
        bot.get_chat(acc["home"]).remove_contact(me)
    except ValueError:
        pass
    try:
        bot.get_chat(acc["notif"]).remove_contact(me)
    except ValueError:
        pass
    db.remove_account(acc["id"])
    replies.add(
        text="You have logged out from: " + acc["api_url"],
        chat=bot.get_chat(acc["addr"]),
    )


def _check_notifications(
    db: DBManager, bot: DeltaBot, acc: sqlite3.Row, m: mastodon.Mastodon
) -> None:
    max_id = None
    dmsgs = []
    notifications = []
    while True:
        ns = m.notifications(max_id=max_id, since_id=acc["last_notif"])
        if not ns:
            break
        if max_id is None:
            db.set_last_notif(acc["id"], ns[0].id)
        max_id = ns[-1]
        for n in ns:
            if (
                n.type == "mention"
                and n.status.visibility == Visibility.DIRECT
                and len(n.status.mentions) == 1
            ):
                dmsgs.append(n.status)
            else:
                notifications.append(n)
    for dm in reversed(dmsgs):
        text = f"{get_name(dm.account)}:\n\n"

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
        text += f"⭐ /m_star_{acc['id']}_{dm.id}\n"

        pv = db.get_pchat_by_contact(acc["id"], dm.account.acct)
        if pv:
            g = bot.get_chat(pv["id"])
            if g is None:
                db.remove_pchat(pv["id"])
            else:
                g.send_text(text)
        else:
            url = rmprefix(acc["api_url"], "https://")
            g = bot.create_group(f"🇲 {dm.account.acct} ({url})", [acc["addr"]])
            db.add_pchat(g.id, dm.account.acct, acc["id"])

            r = requests.get(dm.account.avatar_static)
            with NamedTemporaryFile(
                dir=bot.account.get_blobdir(), suffix=".jpg", delete=False
            ) as file:
                path = file.name
            with open(path, "wb") as file:
                file.write(r.content)
            try:
                g.set_profile_image(path)
            except ValueError as err:
                bot.logger.exception(err)

            g.send_text(text)

    bot.logger.debug(
        "Notifications: %s new entries (last id: %s)",
        len(notifications),
        acc["last_notif"],
    )
    if notifications:
        bot.get_chat(acc["notif"]).send_text(
            TOOT_SEP.join(toots2text(notifications, acc["id"], True))
        )


def _check_home(
    db: DBManager, bot: DeltaBot, acc: sqlite3.Row, m: mastodon.Mastodon
) -> None:
    me = m.me()
    max_id = None
    toots: list = []
    while True:
        ts = m.timeline_home(max_id=max_id, since_id=acc["last_home"])
        if not ts:
            break
        if max_id is None:
            db.set_last_home(acc["id"], ts[0].id)
        max_id = ts[-1]
        for t in ts:
            for a in t.mentions:
                if a.id == me.id:
                    break
            else:
                toots.append(t)
    bot.logger.debug("Home: %s new entries (last id: %s)", len(toots), acc["last_home"])
    if toots:
        bot.get_chat(acc["home"]).send_text(TOOT_SEP.join(toots2text(toots, acc["id"])))


def listen_to_mastodon(db: DBManager, bot: DeltaBot) -> None:
    while True:
        bot.logger.info("Checking Mastodon")
        instances: dict = {}
        for acc in db.get_accounts():
            instances.setdefault(acc["api_url"], []).append(acc)
        while instances:
            for key in list(instances.keys()):
                if not instances[key]:
                    instances.pop(key)
                    continue
                acc = instances[key].pop()
                try:
                    m = get_session(db, acc)
                    _check_notifications(db, bot, acc, m)
                    _check_home(db, bot, acc, m)
                except (mastodon.MastodonUnauthorizedError, mastodon.MastodonAPIError):
                    db.remove_account(acc["id"])
                    bot.get_chat(acc["addr"]).send_text(
                        "ERROR! You have been logged out from: " + acc["api_url"]
                    )
                except Exception as ex:  # noqa
                    bot.logger.exception(ex)
            time.sleep(2)
        time.sleep(int(getdefault(bot, "delay")))


def send_toot(
    masto: mastodon.Mastodon,
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


def get_session(db: DBManager, acc) -> mastodon.Mastodon:
    client = db.get_client(acc["api_url"])
    if client:
        client_id, client_secret = client["id"], client["secret"]
    else:
        client_id, client_secret = mastodon.Mastodon.create_app(
            "DeltaChat Bridge", api_base_url=acc["api_url"]
        )
        db.add_client(acc["api_url"], client_id, client_secret)
    m = mastodon.Mastodon(
        client_id=client_id,
        client_secret=client_secret,
        api_base_url=acc["api_url"],
        ratelimit_method="throw",
    )
    m.log_in(acc["email"], acc["password"])
    return m
