#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import urllib.request

API_URL = "https://api.buffer.com"
TARGET_CHANNELS = {"select_carr", "select_carrr"}


def graphql(query: str) -> dict:
    key = os.getenv("BUFFER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BUFFER_API_KEY is missing")

    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("errors"):
        raise RuntimeError(
            json.dumps(
                payload["errors"],
                ensure_ascii=False,
            )
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        raise RuntimeError(
            "Buffer API returned no data"
        )

    return data


def get_organizations() -> list[dict]:
    data = graphql(
        """
        query GetOrganizations {
          account {
            organizations {
              id
              name
            }
          }
        }
        """
    )

    organizations = (
        (data.get("account") or {})
        .get("organizations")
        or []
    )

    return [
        dict(item)
        for item in organizations
    ]


def get_channels(
    organization_id: str,
) -> list[dict]:

    safe_id = json.dumps(
        str(organization_id)
    )

    data = graphql(
        f"""
        query GetChannels {{
          channels(
            input: {{
              organizationId: {safe_id}
            }}
          ) {{
            id
            name
            displayName
            service
            isQueuePaused
            isDisconnected
            isLocked
          }}
        }}
        """
    )

    return [
        dict(item)
        for item in (
            data.get("channels")
            or []
        )
    ]


def check_buffer() -> int:
    organizations = (
        get_organizations()
    )

    if not organizations:
        raise RuntimeError(
            "No Buffer organization found"
        )

    found: dict[
        str,
        dict,
    ] = {}

    printable = []

    for org in organizations:

        channels = get_channels(
            org["id"]
        )

        for channel in channels:

            item = dict(
                channel
            )

            item[
                "organizationId"
            ] = org["id"]

            item[
                "organizationName"
            ] = (
                org.get("name")
                or ""
            )

            printable.append(
                item
            )

            name = str(
                channel.get("name")
                or ""
            ).strip().lower()

            if name in TARGET_CHANNELS:
                found[name] = item

    for item in printable:

        print(
            json.dumps(
                {
                    "name":
                        item.get("name"),

                    "displayName":
                        item.get(
                            "displayName"
                        ),

                    "service":
                        item.get(
                            "service"
                        ),

                    "isDisconnected":
                        item.get(
                            "isDisconnected"
                        ),

                    "isLocked":
                        item.get(
                            "isLocked"
                        ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    missing = (
        TARGET_CHANNELS
        - set(found)
    )

    if missing:
        raise RuntimeError(
            "Missing Buffer channels: "
            + ", ".join(
                sorted(missing)
            )
        )

    invalid = []

    for name in sorted(
        TARGET_CHANNELS
    ):

        channel = found[name]

        if (
            str(
                channel.get(
                    "service"
                )
                or ""
            ).lower()
            != "instagram"
        ):
            invalid.append(
                f"{name}:not_instagram"
            )

        if (
            channel.get(
                "isDisconnected"
            )
            is True
        ):
            invalid.append(
                f"{name}:disconnected"
            )

        if (
            channel.get(
                "isLocked"
            )
            is True
        ):
            invalid.append(
                f"{name}:locked"
            )

    if invalid:
        raise RuntimeError(
            "Invalid Buffer channels: "
            + ", ".join(
                invalid
            )
        )

    print(
        json.dumps(
            {
                "status": "ok",
                "buffer_api": True,
                "select_carr": True,
                "select_carrr": True,
                "channels_found": 2,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "check_buffer",
        ],
        nargs="?",
        default="check_buffer",
    )

    args = parser.parse_args()

    if (
        args.action
        == "check_buffer"
    ):
        return check_buffer()

    return 2


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
