#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

STAGE4_OUTPUT_DIR = Path(
    os.getenv(
        "STAGE4_OUTPUT_DIR",
        ROOT / "stage4_runtime",
    )
)

MANIFEST_PATH = (
    STAGE4_OUTPUT_DIR
    / "manifest.json"
)

INSTAGRAM_STATE_PATH = (
    STAGE4_OUTPUT_DIR
    / "publish_state.json"
)

BALE_STATE_PATH = (
    STAGE4_OUTPUT_DIR
    / "bale_publish_state.json"
)

BALE_BOT_TOKEN = os.getenv(
    "SELECT_CARR_BALE_BOT_TOKEN",
    "",
).strip()

BALE_CHAT_ID = (
    os.getenv(
        "SELECT_CARR_BALE_CHAT_ID",
        "@select_carr",
    ).strip()
    or "@select_carr"
)

BALE_API_BASE = os.getenv(
    "SELECT_CARR_BALE_API_BASE",
    "https://tapi.bale.ai",
).rstrip("/")

MEDIA_BRANCH = os.getenv(
    "SELECT_CARR_STAGE4_MEDIA_BRANCH",
    "select-carr-stage4-media",
).strip()

TARGET_INSTAGRAM_CHANNELS = (
    "select_carr",
    "select_carrr",
)

FOLLOW_CAPTION = (
    "برای دریافت\n"
    "✅بهترین فرصت های خرید و فروش خودروی خودتان\n"
    "✅و قیمت روز خودروها\n"
    "✅و قیمت طلا و دلار ،\n"
    "حتما با ما همراه باشد .\n"
    "@select_carr\n"
    "@select_carrr"
)

KIND_TITLES = {
    "opportunity":
        "فرصت خرید خودرو",

    "buy_request":
        "درخواست خرید خودرو",

    "market":
        "قیمت طلا، دلار، سکه و اونس",

    "cars":
        "قیمت روز خودروها",
}


def api_url(
    method: str,
) -> str:

    if not BALE_BOT_TOKEN:

        raise RuntimeError(
            "SELECT_CARR_BALE_BOT_TOKEN is missing"
        )

    return (
        f"{BALE_API_BASE}"
        f"/bot{BALE_BOT_TOKEN}"
        f"/{method}"
    )


def api_call(
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:

    request = urllib.request.Request(
        api_url(
            method
        ),
        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type":
                "application/json"
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=45,
        ) as response:

            data = json.loads(
                response
                .read()
                .decode(
                    "utf-8"
                )
            )

    except Exception as exc:

        raise RuntimeError(
            f"Bale API request failed: "
            f"{method}: {exc}"
        ) from exc

    if not data.get(
        "ok"
    ):

        raise RuntimeError(
            "Bale API error: "
            + json.dumps(
                data,
                ensure_ascii=False,
            )
        )

    return data


def check_bale() -> int:

    me = api_call(
        "getMe",
        {},
    )

    chat = api_call(
        "getChat",
        {
            "chat_id":
                BALE_CHAT_ID
        },
    )

    chat_result = (
        chat.get(
            "result"
        )
        or {}
    )

    me_result = (
        me.get(
            "result"
        )
        or {}
    )

    print(
        json.dumps(
            {
                "status":
                    "ok",

                "chat_id":
                    BALE_CHAT_ID,

                "chat_title":
                    chat_result.get(
                        "title"
                    )
                    or chat_result.get(
                        "username"
                    )
                    or "",

                "bot_username":
                    me_result.get(
                        "username"
                    )
                    or "",
            },
            ensure_ascii=False,
        )
    )

    return 0


def load_json_dict(
    path: Path,
    default: dict[str, Any],
) -> dict[str, Any]:

    if not path.is_file():

        return dict(
            default
        )

    try:

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return dict(
            default
        )

    return (
        data
        if isinstance(
            data,
            dict,
        )
        else dict(
            default
        )
    )


def load_bale_state() -> dict[
    str,
    Any,
]:

    state = load_json_dict(
        BALE_STATE_PATH,
        {
            "version":
                1,

            "published":
                {},
        },
    )

    if not isinstance(
        state.get(
            "published"
        ),
        dict,
    ):

        state[
            "published"
        ] = {}

    state.setdefault(
        "version",
        1,
    )

    return state


def save_bale_state(
    state: dict[str, Any],
) -> None:

    STAGE4_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    BALE_STATE_PATH.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def bale_is_published(
    state: dict[str, Any],
    key: str,
) -> bool:

    return (
        key
        in (
            state.get(
                "published"
            )
            or {}
        )
    )


def bale_mark_published(
    state: dict[str, Any],
    key: str,
    result: Any,
) -> None:

    state.setdefault(
        "published",
        {},
    )[
        key
    ] = {
        "result":
            result
    }

    save_bale_state(
        state
    )


def instagram_published(
    key: str,
) -> bool:

    state = load_json_dict(
        INSTAGRAM_STATE_PATH,
        {
            "published":
                {}
        },
    )

    published = (
        state.get(
            "published"
        )
        or {}
    )

    if not isinstance(
        published,
        dict,
    ):

        return False

    return all(
        (
            f"{channel}|{key}"
            in published
        )
        for channel
        in TARGET_INSTAGRAM_CHANNELS
    )


def raw_media_url(
    filename: str,
) -> str:

    repository = os.getenv(
        "GITHUB_REPOSITORY",
        "",
    ).strip()

    if not repository:

        raise RuntimeError(
            "GITHUB_REPOSITORY is missing"
        )

    return (
        "https://raw.githubusercontent.com/"
        + repository
        + "/"
        + MEDIA_BRANCH
        + "/"
        + urllib.parse.quote(
            filename
        )
    )


def caption_for(
    kind: str,
) -> str:

    title = KIND_TITLES.get(
        kind,
        "Select Carr",
    )

    return (
        f"{title}\n\n"
        f"{FOLLOW_CAPTION}"
    )


def send_photo(
    image_url: str,
    caption: str,
) -> Any:

    response = api_call(
        "sendPhoto",
        {
            "chat_id":
                BALE_CHAT_ID,

            "photo":
                image_url,

            "caption":
                caption,
        },
    )

    return response.get(
        "result"
    )


def send_album(
    image_urls: list[str],
    caption: str,
) -> Any:

    if len(
        image_urls
    ) == 1:

        return send_photo(
            image_urls[
                0
            ],
            caption,
        )

    if not (
        2
        <= len(
            image_urls
        )
        <= 10
    ):

        raise RuntimeError(
            "Bale media group must "
            "contain 2 to 10 images"
        )

    media: list[
        dict[str, Any]
    ] = []

    for (
        index,
        url,
    ) in enumerate(
        image_urls
    ):

        item: dict[
            str,
            Any,
        ] = {
            "type":
                "photo",

            "media":
                url,
        }

        if index == 0:

            item[
                "caption"
            ] = caption

        media.append(
            item
        )

    response = api_call(
        "sendMediaGroup",
        {
            "chat_id":
                BALE_CHAT_ID,

            "media":
                media,
        },
    )

    return response.get(
        "result"
    )


def load_manifest() -> dict[
    str,
    Any,
]:

    if not MANIFEST_PATH.is_file():

        raise RuntimeError(
            f"Stage 4 manifest missing: "
            f"{MANIFEST_PATH}"
        )

    data = json.loads(
        MANIFEST_PATH.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "Stage 4 manifest is invalid"
        )

    return data


def publish_story_items(
    manifest: dict[str, Any],
    state: dict[str, Any],
    mode: str,
) -> tuple[
    int,
    int,
    int,
]:

    sent = 0
    duplicates = 0
    waiting = 0

    stories = list(
        manifest.get(
            "stories"
        )
        or []
    )

    if mode == "opportunity":

        stories = [
            item
            for item
            in stories
            if item.get(
                "kind"
            )
            == "opportunity"
        ]

    else:

        stories = (
            stories[
                :1
            ]
        )

    for item in stories:

        key = str(
            item.get(
                "key"
            )
            or ""
        ).strip()

        path = Path(
            str(
                item.get(
                    "path"
                )
                or ""
            )
        )

        kind = str(
            item.get(
                "kind"
            )
            or "story"
        )

        if (
            not key
            or not path.name
        ):

            continue

        if not instagram_published(
            key
        ):

            waiting += 1

            continue

        bale_key = (
            "bale|"
            + key
        )

        if bale_is_published(
            state,
            bale_key,
        ):

            duplicates += 1

            continue

        result = send_photo(
            raw_media_url(
                path.name
            ),
            caption_for(
                kind
            ),
        )

        bale_mark_published(
            state,
            bale_key,
            result,
        )

        sent += 1

    return (
        sent,
        duplicates,
        waiting,
    )


def publish_market(
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> tuple[
    int,
    int,
    int,
]:

    item = manifest.get(
        "market_post"
    )

    if not isinstance(
        item,
        dict,
    ):

        return (
            0,
            0,
            0,
        )

    key = str(
        item.get(
            "key"
        )
        or ""
    ).strip()

    path = Path(
        str(
            item.get(
                "path"
            )
            or ""
        )
    )

    if (
        not key
        or not path.name
    ):

        return (
            0,
            0,
            0,
        )

    if not instagram_published(
        key
    ):

        return (
            0,
            0,
            1,
        )

    bale_key = (
        "bale|"
        + key
    )

    if bale_is_published(
        state,
        bale_key,
    ):

        return (
            0,
            1,
            0,
        )

    result = send_photo(
        raw_media_url(
            path.name
        ),
        caption_for(
            "market"
        ),
    )

    bale_mark_published(
        state,
        bale_key,
        result,
    )

    return (
        1,
        0,
        0,
    )


def publish_cars(
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> tuple[
    int,
    int,
    int,
]:

    slides = list(
        manifest.get(
            "car_price_slides"
        )
        or []
    )

    if not slides:

        return (
            0,
            0,
            0,
        )

    key = str(
        manifest.get(
            "car_price_key"
        )
        or slides[
            0
        ].get(
            "key"
        )
        or ""
    ).strip()

    if not key:

        return (
            0,
            0,
            0,
        )

    if not instagram_published(
        key
    ):

        return (
            0,
            0,
            1,
        )

    bale_key = (
        "bale|"
        + key
    )

    if bale_is_published(
        state,
        bale_key,
    ):

        return (
            0,
            1,
            0,
        )

    urls = []

    for slide in slides[
        :10
    ]:

        path = Path(
            str(
                slide.get(
                    "path"
                )
                or ""
            )
        )

        if path.name:

            urls.append(
                raw_media_url(
                    path.name
                )
            )

    if not urls:

        return (
            0,
            0,
            0,
        )

    result = send_album(
        urls,
        caption_for(
            "cars"
        ),
    )

    bale_mark_published(
        state,
        bale_key,
        result,
    )

    return (
        1,
        0,
        0,
    )


def publish(
    mode: str,
) -> int:

    manifest = (
        load_manifest()
    )

    state = (
        load_bale_state()
    )

    resolved_mode = (
        mode
    )

    if mode == "auto":

        resolved_mode = str(
            manifest.get(
                "mode"
            )
            or ""
        ).strip()

    if resolved_mode in {
        "story_queue",
        "opportunity",
    }:

        (
            sent,
            duplicates,
            waiting,
        ) = publish_story_items(
            manifest,
            state,
            resolved_mode,
        )

    elif resolved_mode == "market":

        (
            sent,
            duplicates,
            waiting,
        ) = publish_market(
            manifest,
            state,
        )

    elif resolved_mode == "cars":

        (
            sent,
            duplicates,
            waiting,
        ) = publish_cars(
            manifest,
            state,
        )

    else:

        print(
            json.dumps(
                {
                    "status":
                        "skipped",

                    "reason":
                        "manifest_mode_not_publishable",

                    "manifest_mode":
                        resolved_mode,
                },
                ensure_ascii=False,
            )
        )

        return 0

    print(
        json.dumps(
            {
                "status":
                    "publish_complete",

                "mode":
                    resolved_mode,

                "chat_id":
                    BALE_CHAT_ID,

                "published_now":
                    sent,

                "skipped_duplicates":
                    duplicates,

                "waiting_for_instagram":
                    waiting,
            },
            ensure_ascii=False,
        )
    )

    return 0


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "check",
            "publish",
        ],
    )

    parser.add_argument(
        "--mode",
        choices=[
            "auto",
            "opportunity",
            "story_queue",
            "market",
            "cars",
        ],
        default="auto",
    )

    args = (
        parser.parse_args()
    )

    if args.action == "check":

        return check_bale()

    return publish(
        args.mode
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
