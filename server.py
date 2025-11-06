from quart import Quart, request, jsonify, redirect, render_template_string
from werkzeug.middleware.proxy_fix import ProxyFix
from agent.core import create_agent
from helpers.google_ads_token import get_google_ads_auth_url, get_google_ads_token
import asyncio
import os


app = Quart(__name__)
app.asgi_app = ProxyFix(app.asgi_app, x_proto=1, x_host=1)

# In-memory storage for active user sessions
user_agents = {}
user_agents_lock = asyncio.Lock()

# TODO: Add background check for inactive user sessions & clear them from memory.

# Main messaging endpoint 
@app.route("/prompt", methods=["POST"])
async def prompt():
    data = await request.get_json()
    prompt = data.get("prompt")
    user_id = data.get("user_id")

    # "refresh" command to reset the user agent's memory (chat history)
    if prompt.lower() == "refresh":
        async with user_agents_lock:
            if user_id in user_agents:
                agent, context, memory = await create_agent()
                user_agents[user_id] = (agent, context, memory, {})
                return jsonify({"response": "Chat history has been refreshed."})

    # "autheticate" command to authenticate user's Google Ads API
    if prompt.lower() == "authenticate":
        BASE_URL = os.getenv("APP_URL", "")
        user_auth_url = f"{BASE_URL}/authenticate?user_id={user_id}"
        return jsonify({"response": f"Please follow this link to authenticate: [Authenticate]({user_auth_url})"}), 200

    # Check for existing user agent session or create a new one.
    async with user_agents_lock:
        if user_id not in user_agents:
            agent, context, memory = await create_agent()
            user_agents[user_id] = (agent, context, memory, {})

        agent, context, memory, _ = user_agents[user_id]

    response = await agent.run(user_msg=prompt, ctx=context, memory=memory)
    return jsonify({"response": str(response)}), 200


@app.route("/authenticate")
async def authenticate():
    user_id = request.args.get("user_id")

    auth_url, state = await get_google_ads_auth_url()

    # Store Google credentials in user's agent lock
    async with user_agents_lock:
        if user_id in user_agents:
            agent, context, memory, google_creds = user_agents[user_id]
        else:
            google_creds = {}
            agent, context, memory = await create_agent()
        google_creds['auth_url'] = auth_url
        google_creds['state'] = state
        google_creds['access_token'] = ""
        google_creds['refresh_token'] = ""
        user_agents[user_id] = (agent, context, memory, google_creds)

    return redirect(auth_url)


@app.route("/callback", methods=["GET", "POST"])
async def callback():
    state = request.args.get("state")
    authorization_response = request.url

    async with user_agents_lock:
        # Find the user associated with this state
        for user_id, (agent, context, memory, google_creds) in user_agents.items():
            if google_creds.get("state") == state:
                if not google_creds.get("refresh_token"):
                    if not google_creds.get("access_token"):
                        credentials = await get_google_ads_token(state, authorization_response)
                        google_creds['access_token'] = credentials.token
                        google_creds['refresh_token'] = credentials.refresh_token
                        await context.store.set("google_refresh_token", credentials.refresh_token)

                # If it's a POST request, the user submitted their customer ID - store it in context for use in tools
                if request.method == "POST":
                    form_data = await request.form
                    customer_id = form_data.get("customer_id").replace("-", "")
                    google_creds["customer_id"] = customer_id
                    await context.store.set("google_customer_id", customer_id)
                    user_agents[user_id] = (agent, context, memory, google_creds)
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
