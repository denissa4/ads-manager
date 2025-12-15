from quart import Quart, request, jsonify, redirect, render_template_string, make_response
from llama_index.core.agent.workflow import AgentStream, ToolCall
from agent.core import create_agent
from helpers.google_ads_token import get_google_ads_auth_url, get_google_ads_token
from helpers.azure_tables import get_user_data, store_user_data
from helpers.file_helpers import handle_attachments
import asyncio
import os
import time
import json
import base64


app = Quart(__name__)

# In-memory storage for active user sessions
user_agents = {}
user_agents_lock = asyncio.Lock()

# Check for inactive sessions, clear them from memory after 30 mins of inactivity.
SESSION_TIMEOUT = 30 * 60

async def cleanup_inactive_sessions():
    while True:
        await asyncio.sleep(300)
        now = time.time()
        async with user_agents_lock:
            inactive_users = [
                user_id for user_id, (_, _, _, _, last_active) in user_agents.items()
                if now - last_active > SESSION_TIMEOUT
            ]
            for user_id in inactive_users:
                _, context, _, _, _ = user_agents[user_id]
                keywords_file = await context.store.get('keywords_search_file', '')
                campaign_ideas_file = await context.store.get('campaign_ideas_file', '')
                uploaded_files = await context.store.get('uploaded_files', [])
                if keywords_file:
                    os.remove(keywords_file)
                if campaign_ideas_file:
                    os.remove(campaign_ideas_file)
                if uploaded_files:
                    for f in uploaded_files:
                        os.remove(f)
                del user_agents[user_id]
                print(f"Cleared inactive session for user: {user_id}")


@app.before_serving
async def startup_tasks():
    app.add_background_task(cleanup_inactive_sessions)
    print("Clean-up task running...")


async def stream_response(agent, full_prompt, context, memory):
    try:
        handler = agent.run(user_msg=full_prompt, ctx=context, memory=memory)
        async for event in handler.stream_events():
            if isinstance(event, AgentStream):
                if event.delta:
                    yield "".join(event.delta)
            elif isinstance(event, ToolCall):
                yield f"\n\n**Using tool: {event.tool_name.replace("_", "-")}**\n\n"
    except Exception as e:
        print(f"Error in LLM response: {e}", flush=True)
        yield f"Error: {e}"


# Main messaging endpoint 
@app.route("/prompt", methods=["POST"])
async def prompt():
    data = await request.get_json()
    prompt = data.get("prompt")
    user_id = data.get("user_id")
    attachments = data.get("attachments")

    # "refresh" command to reset the user agent's memory (chat history)
    if prompt.lower() == "refresh":
        async with user_agents_lock:
            if user_id in user_agents:
                agent, context, memory = await create_agent()
                user_agents[user_id] = (agent, context, memory, {}, time.time())
            return jsonify({"response": "Chat history has been refreshed."}), 200

    # "autheticate" command to authenticate user's Google Ads API
    if prompt.lower() == "authenticate":
        BASE_URL = os.getenv("APP_URL", "")
        url_safe_id = base64.urlsafe_b64encode(user_id.encode()).decode()
        user_auth_url = f"{BASE_URL}/authenticate?userId={url_safe_id}"
        return jsonify({"response": f"Please follow this link to authenticate: [Authenticate]({user_auth_url})"}), 200
    
    # Check for existing user agent session or create a new one.
    async with user_agents_lock:
        if user_id not in user_agents:
            agent, context, memory = await create_agent()
            await context.store.set('user_id', user_id)
            user_agents[user_id] = (agent, context, memory, {}, time.time())
        else:
            # Update timestamp
            agent, context, memory, google_creds, _ = user_agents[user_id]
            user_agents[user_id] = (agent, context, memory, google_creds, time.time())
        agent, context, memory, _, _ = user_agents[user_id]

        # Add google creds from Azure table if they exist
        customer_id, refresh_token = await get_user_data(user_id)
        await context.store.set("user_id", user_id)
        if customer_id:
            await context.store.set("google_customer_id", customer_id)
        if refresh_token:
            await context.store.set("google_refresh_token", refresh_token)
    
    # Parse attachments and extract URLs for downloadable files
    attachments_data = None
    attached_files_data = ""
    attached_file_paths = []
    if attachments:
        attachment_urls = []
        for attachment in attachments:
            if attachment.get('contentType') == 'text/html':
                continue
            content = attachment.get('content', {})
            content_url = content.get('downloadUrl', '')
            attachment_name = attachment.get('name', 'unknown.txt')
            attachment_urls.append({"url": content_url, "name": attachment_name})
            if content_url:
                attachments_data = await handle_attachments(user_id, attachment_urls)
        if attachments_data:
            for data in attachments_data:
                filename = data.get('filename', '')
                content = data.get('text', '')
                file_path = data.get('file_path', '')

                attached_files_data += f"--- {filename} ---\n {content}\n---\n\n"
                attached_file_paths.append(file_path)
            await context.store.set("uploaded_files", attached_file_paths)

    prompt_ext = ""
    keywords_file = await context.store.get('keywords_search_file', '')
    campaign_ideas_file = await context.store.get('campaign_ideas_file', '')

    if keywords_file:
        prompt_ext += f"Keyword search file path (use as reference data for Google Ads Campaign generation and adding keywords to existing ads.): {keywords_file}\n"
    if campaign_ideas_file:
        prompt_ext += f"Campaign ideas file path: {campaign_ideas_file}\n"
    if attached_files_data:
        prompt_ext += f"User's uploaded attachments data (use as reference data for Google Ads Campaign generation): {attached_files_data}\n"
    
    full_prompt = ""
    if prompt_ext:
        full_prompt = f"SYSTEM: {prompt_ext}\n\nUSER: {prompt}"
    else:
        full_prompt = prompt

    async def generate():
        try:
            async for chunk in stream_response(agent, full_prompt, context, memory):
                if chunk:
                    yield (json.dumps({"response": chunk}) + "\n").encode("utf-8")
                    await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            yield json.dumps({"response": "stream cancelled"}) + "\n"
        except Exception as e:
            yield json.dumps({"response": str(e)}) + "\n"

    res = await make_response(generate())
    res.timeout = None
    return res


@app.route("/authenticate")
async def authenticate():
    raw = request.args.get("userId", "")
    user_id = base64.urlsafe_b64decode(raw.encode()).decode()

    auth_url, state = await get_google_ads_auth_url()

    # Store Google credentials in user's agent lock
    async with user_agents_lock:
        if user_id in user_agents:
            agent, context, memory, google_creds, _ = user_agents[user_id]
        else:
            google_creds = {}
            agent, context, memory = await create_agent()
        google_creds['auth_url'] = auth_url
        google_creds['state'] = state
        google_creds['access_token'] = ""
        google_creds['refresh_token'] = ""
        user_agents[user_id] = (agent, context, memory, google_creds, time.time())

    return redirect(auth_url)


@app.route("/callback", methods=["GET", "POST"])
async def callback():
    state = request.args.get("state")
    authorization_response = request.url

    async with user_agents_lock:
        # Find the user associated with this state
        for user_id, (agent, context, memory, google_creds, _) in user_agents.items():
            if google_creds.get("state") == state:
                if not google_creds.get("refresh_token"):
                    if not google_creds.get("access_token"):
                        credentials = await get_google_ads_token(state, authorization_response)
                        google_creds['access_token'] = credentials.token
                        google_creds['refresh_token'] = credentials.refresh_token
                        # Store user's data in Azure table
                        stored = await store_user_data(user_id, google_creds)
                        if stored:
                            print("User data stored successfully.")
                        else:
                            print("There was an issue storing the user data.")
                        
                        await context.store.set("google_refresh_token", credentials.refresh_token)

                # If it's a POST request, the user submitted their customer ID - store it in context for use in tools
                if request.method == "POST":
                    form_data = await request.form
                    customer_id = form_data.get("customer_id").replace("-", "")
                    google_creds["customer_id"] = customer_id
                    # Store user's data in Azure table
                    stored = await store_user_data(user_id, google_creds)
                    if stored:
                        print("User data stored successfully.")
                    else:
                        print("There was an issue storing the user data.")
                    await context.store.set("google_customer_id", customer_id)

                    user_agents[user_id] = (agent, context, memory, google_creds, time.time())

                    return f"""
                        <html>
                        <body style='font-family: sans-serif;'>
                            <h2>✅ Setup complete!</h2>
                            <p>Customer ID saved. You can now return to the app and continue.</p>
                        </body>
                        </html>
                    """

                # Otherwise, show form to enter customer ID
                return await render_template_string("""
                    <html>
                    <body style='font-family: sans-serif;'>
                        <h2>Google Ads Authentication Successful!</h2>
                        <p>Please enter your Google Ads Customer ID to complete setup:</p>
                        <form method="post">
                            <input type="text" name="customer_id" placeholder="123-456-7890" required>
                            <button type="submit">Submit</button>
                        </form>
                    </body>
                    </html>
                """)
        else:
            return f"""
                <html>
                <body style='font-family: sans-serif;'>
                    <p>Authentication failed.</p>
                    <p>Invalid State. Please contact an admin if the issue persists.</p>
                </body>
                </html>
            """
