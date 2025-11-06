from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.core.agent.workflow import FunctionAgent, AgentWorkflow
from llama_index.core.workflow import Context
import os


async def add(a: int, b: int) -> str:
    return str(a + b)


async def create_agent():
    try:
        llm = BedrockConverse(
            model=os.getenv("AWS_MODEL_NAME", ""),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region_name=os.getenv("AWS_DEFAULT_REGION", ""),
            max_tokens=int(os.getenv("MAX_TOKENS", 12000)),
        )

        with open("/app/agent/system_prompt.md", 'r') as f:
            system_prompt = f.read()

        agent = FunctionAgent(
            tools=[add],
            llm=llm,
            system_prompt=system_prompt,
        )

        ctx = Context(agent)

        workflow = AgentWorkflow(agents=[agent])

        return workflow, ctx
    except Exception as e:
        print(f"Error creating agent: {e}")
        return None