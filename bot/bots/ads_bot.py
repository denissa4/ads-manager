# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

from typing import List
import aiohttp
import os
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Attachment, Activity
from botframework.connector.auth import MicrosoftAppCredentials



class AdsBot(ActivityHandler):
    async def on_members_added_activity(
        self, members_added: List[ChannelAccount], turn_context: TurnContext
    ):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity("Hi there! I’m your Google Ads Campaign Assistant. " \
                                                "I'll help you design, optimize, manage, and launch your Google Ads Campaigns. " \
                                                'To get started type "authenticate" into the chat to authorise the use of the Google Ads API.')
                

    async def send_to_backend(self, prompt: str, user_id: str, attachments: list) -> str:
        url = "http://127.0.0.1:8000/prompt"

        # Convert Attachment objects to dicts
        serialized_attachments = []
        for att in attachments:
            serialized_attachments.append({
                "contentType": att.content_type,
                "contentUrl": getattr(att, "content_url", None),
                "name": getattr(att, "name", None),
                "content": getattr(att, "content", None),
            })

        payload = {
            "prompt": prompt,
            "user_id": user_id,
            "attachments": serialized_attachments,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("response", "No response from backend.")
                    else:
                        return f"Backend error: {resp.status}"
        except Exception as e:
            return f"Error contacting backend: {e}"


    async def on_message_activity(self, turn_context: TurnContext):

        user_prompt = turn_context.activity.text
        user_id = turn_context.activity.from_property.id
        attachments = turn_context.activity.attachments or []

        response = await self.send_to_backend(user_prompt, user_id, attachments)

        response_card = {
            "type": "AdaptiveCard",
            "$schema": "https://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.5",
            "body": [
                {
                "type": "TextBlock",
                "text": response,
                "wrap": True
                },
                {
                    "type": "TextBlock",
                    "text": "AI-generated content cant be incorrect.",
                    "wrap": True,
                    "horizontalAlignment": "Right",
                    "weight": "Lighter",
                    "isSubtle": True
                }
            ]
        }

        card_attachment = Attachment(
            content_type="application/vnd.microsoft.card.adaptive",
            content=response_card
        )

        await turn_context.send_activity(
            Activity(
                type="message",
                attachments=[card_attachment]
            )
        )