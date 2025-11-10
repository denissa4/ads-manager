from google.ads.googleads.client import GoogleAdsClient
from llama_index.core.workflow import Context
from llama_index.core.llms import ChatMessage
from helpers.file_helpers import create_keyword_report_file, file_to_text, create_ads_campaign_file
from . import core
import os


DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
MANAGER_ID = os.getenv("GOOGLE_ADS_MANAGER_ID")

APP_URL = os.getenv("APP_URL", "")


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
        
        report_download_url, report_file_path = await create_keyword_report_file(results)
        await ctx.store.set('keywords_search_file', report_file_path)

        return f"In-depth keyword statistics spreadsheet download URL:\n{report_download_url}\n\nKeyword search data:\n{str(results)}."
    except Exception as e:
        print(e)
        return str(e)
    

async def create_campaign_ideas_report(ctx: Context, reference_data_file: str, n_ideas: int) -> str:
    '''
    Uses an LLM to generate n Google Ads Campaign ideas based on the given reference data

    Args:
        - reference_data_file (str): The file path to the user's reference data used to generate Google Ads Campaign ideas (can be a keyword statistics spreadsheet or the user's own data).
        - n_idead (int): The number of Google Ads Campaign ideas the LLM should generate.
    Returns:
        - (str): The download URL to the generated Google Ads Campaign ideas file.
    '''
    llm = await core.get_llm()

    reference_data = file_to_text(reference_data_file)

    with open('/app/agent/system_prompt.md', 'r') as f:
        system_prompt_guidelines = f.read()

    system_prompt = f"You are a helpful assistant. Your job is to generate exactly {n_ideas} Google Ads Campaign ideas based on the user's reference data. Use these guidelines to help you:\n\n{system_prompt_guidelines}"

    messages = [ChatMessage(role="user", content=f"Reference Data:\n\n{reference_data}"), 
                ChatMessage(role="system", content=system_prompt)]
    
    response = await llm.achat(messages)

    download_url, file_path = create_ads_campaign_file(str(response))
    await ctx.store.set('campaign_ideas_file', file_path)

    return f"Google Ads Campaign ideas download URL: {download_url}"