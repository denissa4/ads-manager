from quart import Quart, request, jsonify, redirect, render_template_string
from agent.core import create_agent
from helpers.google_ads_token import get_google_ads_auth_url, get_google_ads_token
from helpers.azure_tables import get_user_data, store_user_data
import asyncio
import os
import time


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
                del user_agents[user_id]
                print(f"Cleared inactive session for user: {user_id}")

@app.before_serving
async def startup_tasks():
    app.add_background_task(cleanup_inactive_sessions)
    print("Clean-up task running...")


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
                user_agents[user_id] = (agent, context, memory, {}, time.time())
                return jsonify({"response": "Chat history has been refreshed."}), 200

    # "autheticate" command to authenticate user's Google Ads API
    if prompt.lower() == "authenticate":
        BASE_URL = os.getenv("APP_URL", "")
        user_auth_url = f"{BASE_URL}/authenticate?user_id={user_id}"
        return jsonify({"response": f"Please follow this link to authenticate: [Authenticate]({user_auth_url})"}), 200

    # Check for existing user agent session or create a new one.
    async with user_agents_lock:
        if user_id not in user_agents:
            agent, context, memory = await create_agent()
            user_agents[user_id] = (agent, context, memory, {}, time.time())
        else:
            # Update timestamp
            agent, context, memory, google_creds, _ = user_agents[user_id]
            user_agents[user_id] = (agent, context, memory, google_creds, time.time())
        agent, context, memory, _, _ = user_agents[user_id]

        # Add google creds from Azure table if they exist
        customer_id, access_token, refresh_token = await get_user_data(user_id)
        await context.store.set("user_id", user_id)
        if customer_id:
            await context.store.set("google_customer_id", customer_id)
        if access_token:
            await context.store.set("google_access_token", access_token)
        if refresh_token:
            await context.store.set("google_refresh_token", refresh_token)


    response = await agent.run(user_msg=prompt, ctx=context, memory=memory)
    return jsonify({"response": str(response)}), 200


@app.route("/authenticate")
async def authenticate():
    user_id = request.args.get("user_id")

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
        for user_id, (agent, context, memory, google_creds, last_active) in user_agents.items():
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
