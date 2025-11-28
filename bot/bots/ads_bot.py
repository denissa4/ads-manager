# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

from typing import List
import aiohttp
import os
import json
from botbuilder.core import ActivityHandler, TurnContext, MessageFactory
from botbuilder.schema import ChannelAccount, Attachment, Activity



STREAMING = os.getenv('STREAMING', 'false').lower() == 'true'

class AdsBot(ActivityHandler):
    async def on_members_added_activity(
        self, members_added: List[ChannelAccount], turn_context: TurnContext
    ):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity("Hi there! I’m your Google Ads Campaign Assistant. " \
                                                "I'll help you design, optimize, manage, and launch your Google Ads Campaigns. " \
                                                'To get started type "authenticate" into the chat to authorise the use of the Google Ads API.')
                

    async def send_to_backend(self, prompt: str, user_id: str, attachments: list):
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
                async with session.post(url, json=payload, timeout=None) as resp:
                    if resp.status != 200:
                        yield f"Backend error: {resp.status}"
                    buffer = ""
                    async for chunk in resp.content.iter_chunked(1024):
                        if not chunk:
                            continue
                        text = chunk.decode("utf-8", errors="ignore")
                        buffer += text

                        # Try to split buffer into lines and parse JSON
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                # Yield the 'response' field
                                if "response" in data:
                                    yield data["response"]
                            except json.JSONDecodeError:
                                # Not a full JSON yet, wait for more chunks
                                buffer = line + "\n" + buffer
                                break
                    # Handle leftover buffer at the end
                    if buffer.strip():
                        try:
                            data = json.loads(buffer)
                            if "response" in data:
                                yield data["response"]
                        except json.JSONDecodeError:
                            yield buffer.strip()
        except Exception as e:
            yield f"Error contacting backend: {e}"


    async def on_message_activity(self, turn_context: TurnContext):
        try:

            user_prompt = turn_context.activity.text
            user_id = turn_context.activity.from_property.id
            attachments = turn_context.activity.attachments or []

            output_text = ""

            initial_card = {
                "type": "AdaptiveCard",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "Thinking...",
                        "wrap": True,
                        "color": "Default",
                    },
                    {
                        "type": "TextBlock",
                        "text": "AI-generated content can be incorrect.",
                        "wrap": True,
                        "horizontalAlignment": "Right",
                        "size": "Small",
                        "weight": "Lighter",
                        "color": "Default"
                    }
                ],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }

            card_attachment = Attachment(content_type="application/vnd.microsoft.card.adaptive", content=initial_card)
            activity = MessageFactory.attachment(card_attachment)
            sent = await turn_context.send_activity(activity)
            activity_id = getattr(sent, 'id', None)

            async for chunk in self.send_to_backend(user_prompt, user_id, attachments):
                output_text += chunk

                updated_card = {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": output_text,
                            "wrap": True,
                            "color": "Default",
                        },
                        {
                            "type": "TextBlock",
                            "text": "AI-generated content can be incorrect.",
                            "wrap": True,
                            "horizontalAlignment": "Right",
                            "size": "Small",
                            "weight": "Lighter",
                            "color": "Default"
                        }
                    ],
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.5"
                }
                updated_attachment = Attachment(
                    content_type="application/vnd.microsoft.card.adaptive", 
                    content=updated_card
                )
                update_activity = Activity(
                    id=activity_id,
                    type="message",
                    attachments=[updated_attachment]
                )
                if STREAMING:
                    await turn_context.update_activity(update_activity)

            if not STREAMING:
                await turn_context.update_activity(update_activity)
        except Exception as e:
            await turn_context.send_activity(str(e))
