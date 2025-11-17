from google.ads.googleads.client import GoogleAdsClient
from llama_index.core.workflow import Context
from llama_index.core.llms import ChatMessage
from helpers.file_helpers import create_keyword_report_file, file_to_text, create_ads_campaign_file, sanitize_text
from . import core
import os
import re
import time


DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
MANAGER_ID = os.getenv("GOOGLE_ADS_MANAGER_ID")

APP_URL = os.getenv("APP_URL", "")


async def google_ads_keyword_search(ctx: Context, keywords: list):
    '''Conducts a Google Ads Keyword search and returns keyword stats.'''
    try:
        refresh_token = await ctx.store.get("google_refresh_token", "")
        customer_id = await ctx.store.get('google_customer_id', "")
        user_id = await ctx.store.get("user_id", "")

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
    with open('/app/agent/campaign_ideas_layout.md', 'r') as f:
        campaign_ideas_example = f.read()

    system_prompt = f"You are a helpful assistant. Your job is to generate exactly {n_ideas} Google Ads Campaign ideas based on the user's reference data. Use these guidelines to help you:\n\n{system_prompt_guidelines}.\n\n" \
    f"Your campaign ideas should follow this exact layout example:\n\n{campaign_ideas_example}.\n\nFinal URL should be https://www.example.com for all campaigns."

    messages = [ChatMessage(role="user", content=f"Reference Data:\n\n{reference_data}"), 
                ChatMessage(role="system", content=system_prompt)]
    
    response = await llm.achat(messages)

    download_url, file_path = create_ads_campaign_file(str(response))
    await ctx.store.set('campaign_ideas_file', file_path)

    return f"Google Ads Campaign ideas download URL: {download_url}. Next see if the user would like to select a campaign from the generated ideas and use the generate_search_campaign function to do this"


async def generate_search_campaign(ctx: Context, ideas_file: str, selected_campaign: str) -> str:
    """
    Creates a fully detailed Google Search campaign with:
    - Budget extracted from ideas file
    - Campaign settings
    - Ad group
    - Keywords
    - Responsive Search Ad
    """

    try:
        # --- Load authentication ---
        refresh_token = await ctx.store.get("google_refresh_token", "")
        customer_id = await ctx.store.get("google_customer_id", "")
        user_id = await ctx.store.get("user_id", "")

        if not refresh_token or not customer_id:
            return f"Authenticate here: {APP_URL}/authenticate?user_id={user_id}"

        # Build client
        credentials = {
            "developer_token": DEVELOPER_TOKEN,
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "use_proto_plus": True,
            "login_customer_id": MANAGER_ID,
        }
        client = GoogleAdsClient.load_from_dict(credentials)

        # --- Load idea block ---
        idea_text = file_to_text(ideas_file)
        if selected_campaign.lower() not in idea_text.lower():
            return "Campaign idea not found in file."

        selected_block = ""
        for block in idea_text.split("\n\n"):
            if selected_campaign.lower() in block.lower():
                selected_block = block
                break

        if not selected_block:
            return "Could not extract campaign section."

        # ---------------------------------------------------------------------
        # 1. Extract Budget From Idea Block
        # ---------------------------------------------------------------------
        budget_match = re.search(
            r"Budget:\s*£?(\d+(?:\.\d+)?)(?:\s*/\s*day)?",
            selected_block,
            re.IGNORECASE,
        )
        budget_daily = float(budget_match.group(1)) if budget_match else 5.0
        budget_micros = int(budget_daily * 1_000_000)

        # ---------------------------------------------------------------------
        # 2. Create Campaign Budget (unique name)
        # ---------------------------------------------------------------------
        budget_service = client.get_service("CampaignBudgetService")
        budget_operation = client.get_type("CampaignBudgetOperation")
        budget = budget_operation.create
        budget.name = f"AI Budget – {selected_campaign} – {int(time.time())}"
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.amount_micros = budget_micros

        budget_response = budget_service.mutate_campaign_budgets(
            customer_id=customer_id,
            operations=[budget_operation],
        )
        budget_resource_name = budget_response.results[0].resource_name

        # ---------------------------------------------------------------------
        # 3. Create Search Campaign
        # ---------------------------------------------------------------------
        campaign_service = client.get_service("CampaignService")
        campaign_operation = client.get_type("CampaignOperation")
        campaign = campaign_operation.create
        campaign.name = f"AI Search – {selected_campaign} - {int(time.time())}"
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.campaign_budget = budget_resource_name
        campaign.contains_eu_political_advertising = (
            client.enums.EuPoliticalAdvertisingStatusEnum
            .DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )

        # Manual CPC bidding (enhanced CPC disabled)
        campaign.manual_cpc.enhanced_cpc_enabled = False

        # Networks
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign.network_settings.target_partner_search_network = False

        # Languages & Targeting restrictions
        targeting = client.get_type("TargetRestriction")  # create TargetRestriction
        targeting.targeting_dimension = client.enums.TargetingDimensionEnum.AUDIENCE
        targeting.bid_only = False
        campaign.targeting_setting.target_restrictions.append(targeting)

        # Geo Targeting: UK
        geo_target_constant_service = client.get_service("GeoTargetConstantService")
        location_info = client.get_type("LocationInfo")
        location_info.geo_target_constant = geo_target_constant_service.geo_target_constant_path("2840")
        campaign.geo_target_type_setting.positive_geo_target_type = (
            client.enums.PositiveGeoTargetTypeEnum.PRESENCE_OR_INTEREST
        )

        campaign_response = campaign_service.mutate_campaigns(
            customer_id=customer_id,
            operations=[campaign_operation],
        )
        campaign_resource = campaign_response.results[0].resource_name

        # ---------------------------------------------------------------------
        # 4. Create Ad Group
        # ---------------------------------------------------------------------
        ad_group_service = client.get_service("AdGroupService")
        ad_group_operation = client.get_type("AdGroupOperation")
        ad_group = ad_group_operation.create
        ad_group.name = f"{selected_campaign} – Ad Group 1"
        ad_group.campaign = campaign_resource
        ad_group.status = client.enums.AdGroupStatusEnum.ENABLED
        ad_group.cpc_bid_micros = 1_500_000  # £1.50

        ad_group_response = ad_group_service.mutate_ad_groups(
            customer_id=customer_id,
            operations=[ad_group_operation],
        )
        ad_group_resource = ad_group_response.results[0].resource_name

        # ---------------------------------------------------------------------
        # 5. Add Keywords
        # ---------------------------------------------------------------------
        keyword_service = client.get_service("AdGroupCriterionService")

        # Extract keywords from selected block
        keywords = []

        # Look for the Keywords: section until the next section (Headlines, Descriptions, Final URL, or end of block)
        keywords_section = re.search(
            r"Keywords:\s*(.*?)\n(?:Headlines:|Descriptions:|Final URL:|$)",
            selected_block,
            re.DOTALL | re.IGNORECASE
        )
        if keywords_section:
            # Each line starting with "-" is a keyword
            for line in keywords_section.group(1).splitlines():
                line = line.strip()
                if line.startswith("-"):
                    # Remove the dash and any leading/trailing spaces
                    keywords.append(line[1:].strip())

        # If no keywords were found, fallback to the campaign name (should rarely happen)
        if not keywords:
            keywords = [selected_campaign]

        # Add keywords to the ad group
        keyword_ops = []
        for kw in keywords:
            op = client.get_type("AdGroupCriterionOperation")
            criterion = op.create
            criterion.ad_group = ad_group_resource
            criterion.keyword.text = kw
            criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum.PHRASE
            criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            keyword_ops.append(op)

        keyword_service.mutate_ad_group_criteria(
            customer_id=customer_id,
            operations=keyword_ops,
        )
        # ---------------------------------------------------------------------
        # 6. Create Responsive Search Ad (RSA)
        # ---------------------------------------------------------------------
        ad_service = client.get_service("AdGroupAdService")
        ad_operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = ad_operation.create
        ad_group_ad.ad_group = ad_group_resource
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED

        rsa = ad_group_ad.ad.responsive_search_ad

        # Properly create AdTextAsset objects
        headlines = [
            selected_campaign[:30],
            "Shop Online Today",
            "Great Deals Available"
        ]
        descriptions = [
            selected_block[:90],
            "Fast delivery and secure checkout."
        ]

        for text in headlines:
            asset = client.get_type("AdTextAsset")
            asset.text = sanitize_text(text)
            rsa.headlines.append(asset)

        for text in descriptions:
            asset = client.get_type("AdTextAsset")
            asset.text = sanitize_text(text)
            rsa.descriptions.append(asset)


        # Placeholder URL
        ad_group_ad.ad.final_urls.append("https://example.com")

        ad_service.mutate_ad_group_ads(
            customer_id=customer_id,
            operations=[ad_operation],
        )

        return (
            "Search campaign created successfully!\n"
            f"- Budget: £{budget_daily}/day\n"
            f"- Campaign: {campaign_resource}\n"
            f"- Ad Group: {ad_group_resource}\n"
            f"- Keywords: {', '.join(keywords)}\n"
            "Ad is paused for review."
        )

    except Exception as e:
        print(e)
        return f"Error creating search campaign: {e}"
