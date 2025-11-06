from quart import Quart, request, jsonify, Response, make_response
from agent.core import create_agent
import asyncio


app = Quart(__name__)

agent, context = asyncio.run(create_agent())

@app.route("/prompt", methods=["POST"])
async def prompt():
    # agent, context = await create_agent()
    data = await request.get_json()
    prompt = data.get("prompt")
    response = await agent.run(user_msg=prompt, ctx=context)
    return jsonify({"response": str(response)}), 200