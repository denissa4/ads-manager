from google.ads.googleads.client import GoogleAdsClient
from llama_index.core.llms import ChatMessage
from llama_index.core.workflow import Context
import aiofiles
import uuid
import os

DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
MANAGER_ID = os.getenv("GOOGLE_ADS_MANAGER_ID")

APP_URL = os.getenv("APP_URL", "")

FILE_SERVE_DIR = '/var/www/html/bot/static/files'


async def google_ads_keyword_search(ctx: Context, keywords: list):
    '''Conducts a Google Ads Keyword search and returns keyword stats.'''
    try:
        refresh_token = await ctx.store.get("google_refresh_token")
        customer_id = await ctx.store.get('google_customer_id')
        user_id = await ctx.store.get("user_id")

        # Check user is authenticated, if not, send the auth link.
        if not refresh_token or not customer_id:
            return f"To use this tool, the user must authenticate via this link: {APP_URL}/authenticate?user_id={user_id}"

        credentials = {
            "developer_token": DEVELOPER_TOKEN,
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "use_proto_plus": True,
            "login_customer_id": MANAGER_ID
        }

        client = GoogleAdsClient.load_from_dict(credentials)

        # Begin keyword search
        keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

        language_id = "1000" # 1000 = Engish
        language_resource_name = f"languageConstants/{language_id}"

        uk_geo_target_id = "2840" # 2840 = UK
        geo_target_resource_name = f"geoTargetConstants/{uk_geo_target_id}"

        # Build request
        request = client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = customer_id
        request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS
        request.language = language_resource_name
        request.geo_target_constants.append(geo_target_resource_name)
        request.keyword_seed.keywords.extend(keywords)

        response = keyword_plan_idea_service.generate_keyword_ideas(request=request)

        results = []
        for idea in response:
            data = {
                "keyword": idea.text,
                "avg_monthly_searches": idea.keyword_idea_metrics.avg_monthly_searches,
                "competition": idea.keyword_idea_metrics.competition.name,
                "low_top_of_page_bid": idea.keyword_idea_metrics.low_top_of_page_bid_micros,
                "high_top_of_page_bid": idea.keyword_idea_metrics.high_top_of_page_bid_micros,
                "competition_index": idea.keyword_idea_metrics.competition_index
            }
            results.append(data)

        if not results:
            return "No keyword data found for the provided search terms. Please try different keywords."
        
        # Get 3 LLM generated campaign ideas/ strategies to add to the report
        with open('/app/agent/system_prompt.md', 'r') as f:
            system_prompt = f.read()
        messages = [
            ChatMessage(role="system", content=f"You are a helpful assistant whose job is to generate 3 in-depth Google Ads Campaign ideas based on the given keyword research results given by the user. Use the following as a reference to help you complete your task: {system_prompt}"),
            ChatMessage(role="user", content=f"Keyword Search Results: {str(results)}")
        ]
        ctx.agent.llm.chat()

        report_download_url = await create_keyword_report_file(results)

        return f"Report download URL:\n{report_download_url}\n\nKeyword search data:\n{str(results)}"
    except Exception as e:
        print(e)
        return str(e)
    

async def create_keyword_report_file(data: list):
    file_name = f"{str(uuid.uuid4())[:6]}_keyword_report.txt"
    file_path = f"{FILE_SERVE_DIR}/{file_name}"

    os.makedirs(FILE_SERVE_DIR, exist_ok=True)

    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        for result in data:
            await f.write(f"Keyword: {result.get('keyword', '')}\n")
            await f.write(f"Average Monthly Searches: {result.get('avg_monthly_searches', 'N/A')}\n")
            await f.write(f"Competition: {result.get('competition', 'N/A')}\n")
            await f.write(f"Competition Index: {result.get('competition_index', 'N/A')}\n")
            await f.write(f"Low Top of Page Bid: {result.get('low_top_of_page_bid', 'N/A')}\n")
            await f.write(f"High Top of Page Bid: {result.get('high_top_of_page_bid', 'N/A')}\n")
            await f.write("-" * 40 + "\n")

    return f"{APP_URL}/downloads/{file_name}"