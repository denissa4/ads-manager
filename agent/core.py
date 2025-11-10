from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.core.agent.workflow import FunctionAgent, AgentWorkflow
from llama_index.core.workflow import Context
from llama_index.core.memory import Memory
import os
from . import tools

async def get_llm():
    return BedrockConverse(
            model=os.getenv("AWS_MODEL_NAME", ""),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region_name=os.getenv("AWS_DEFAULT_REGION", ""),
            max_tokens=int(os.getenv("MAX_TOKENS", 32000)),
            timeout=300.00
        )

async def create_agent():
    try:
        llm = await get_llm()

        with open("/app/agent/system_prompt.md", 'r') as f:
            system_prompt = f.read()

        agent = FunctionAgent(
            tools=[tools.google_ads_keyword_search, tools.create_campaign_ideas_report],
            llm=llm,
            system_prompt=system_prompt,
        )

        ctx = Context(agent)

        memory = Memory.from_defaults(token_limit=10000)

        workflow = AgentWorkflow(agents=[agent], timeout=300.00)

        return workflow, ctx, memory
    except Exception as e:
        print(f"Error creating agent: {e}")
        return None