from google.ads.googleads.client import GoogleAdsClient
from llama_index.core.workflow import Context
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

        # Build request
        request = client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = customer_id
        request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS
        request.language = language_resource_name
        # if geo_target_ids:
        #     request.geo_target_constants.extend([f"geoTargetConstants/{geo_id}" for geo_id in geo_target_ids])
        request.keyword_seed.keywords.extend(keywords)

        response = keyword_plan_idea_service.generate_keyword_ideas(request=request)

        results = []
        for idea in response:
            data = {
                "keyword": idea.text,
                "avg_monthly_searches": idea.keyword_idea_metrics.avg_monthly_searches,
                "competition": idea.keyword_idea_metrics.competition.name,
            }
            results.append(data)

        return str(results)
    except Exception as e:
        print(e)