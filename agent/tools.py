import asyncio
import functools
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


async def run_blocking(func, *args, **kwargs):
    """
    Run a blocking function in the default ThreadPoolExecutor and return result.
    Use this to wrap any blocking I/O, network calls that are not async, CPU heavy tasks, etc.
    """
    loop = asyncio.get_running_loop()
    partial = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, partial)


async def google_ads_keyword_search(ctx: Context, keywords: list) -> str:
    '''Conducts a Google Ads Keyword search and returns keyword stats, 100 results per input keyword.'''
    try:
        refresh_token = await ctx.store.get("google_refresh_token", "")
        customer_id = await ctx.store.get('google_customer_id', "")
        user_id = await ctx.store.get("user_id", "")

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
        keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

        language_id = "1000"  # English
        language_resource_name = f"languageConstants/{language_id}"

        uk_geo_target_id = "2840"  # UK
        geo_target_resource_name = f"geoTargetConstants/{uk_geo_target_id}"

        results = []

        for seed_word in keywords:
            # Build request per keyword
            request = client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = customer_id
            request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS
            request.language = language_resource_name
            request.geo_target_constants.append(geo_target_resource_name)
            request.keyword_seed.keywords.append(seed_word)

            response = await run_blocking(keyword_plan_idea_service.generate_keyword_ideas, request=request)
            
            # Limit to 100 results per seed word
            for idea in list(response)[:100]:
                metrics = idea.keyword_idea_metrics
                results.append({
                    "keyword": idea.text,
                    "avg_monthly_searches": metrics.avg_monthly_searches,
                    "competition": metrics.competition.name,
                    "low_top_of_page_bid": metrics.low_top_of_page_bid_micros,
                    "high_top_of_page_bid": metrics.high_top_of_page_bid_micros,
                    "competition_index": metrics.competition_index,
                    "seed_word": seed_word  # track which seed word produced it
                })
            await asyncio.sleep(1)

        if not results:
            return "No keyword data found for the provided search terms. Please try different keywords."

        report_download_url, report_file_path = await create_keyword_report_file(results)
        await ctx.store.set('keywords_search_file', report_file_path)

        res = (
            f"In-depth keyword statistics spreadsheet download URL:\n{report_download_url}\n\n"
            f"Keyword search data file path:\n{report_file_path}\n\n"
            f"Keyword search data:\n{str(results)}."
        )

        print(len(res.split()))
        return res

    except Exception as e:
        print(e)
        return f"{str(e)}\n\nIf the error is about authentication, the user must authenticate via this link: {APP_URL}/authenticate?user_id={user_id}"


async def create_campaign_ideas_report(ctx: Context, additional_notes: str, n_ideas: int) -> str:
    '''
    Uses an LLM to generate n Google Ads Campaign ideas based on the given reference data. Keyword searches and user uploads are automatically given to the LLM.
    Use additional_notes for any requests the user has about the campaign idea generation.
    '''
    try:
        llm = await core.get_llm()

        reference_data_file_paths = []
        keywords_file = await ctx.store.get('keywords_search_file', '')
        uploaded_files = await ctx.store.get('uploaded_files', '')

        if keywords_file:
            reference_data_file_paths.append(keywords_file)
        if uploaded_files:
            reference_data_file_paths.extend(uploaded_files)

        reference_data_parts = []
        for data_file in reference_data_file_paths:
            text = await run_blocking(file_to_text, data_file)
            reference_data_parts.append(text)

        reference_data = "\n\n".join(reference_data_parts)

        # Read prompts/templates from files
        def read_file(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

        content_gen_prompt = await run_blocking(read_file, "/app/agent/ai_content_generation_prompt.md")
        campaign_ideas_example = await run_blocking(read_file, "/app/agent/campaign_ideas_layout.md")
        template_instructions = await run_blocking(read_file, "/app/agent/template_instructions.md")

        system_prompt = (
            f"You are a helpful assistant. Your job is to generate exactly {n_ideas} Google Ads Campaign ideas "
            f"based on the user's reference data.\n\nUse these guidlines as a reference:\n\n{content_gen_prompt}\n\n"
            f"Your campaign ideas should follow this exact layout example:\n\n{campaign_ideas_example}, be sure strictly to adhere to these rules when creating the template:\n\n{template_instructions}\nAdditional Notes:\n{additional_notes}\n"
            "Final URL should be https://www.example.com for all campaigns."
        )

        messages = [
            ChatMessage(role="user", content=f"Reference Data:\n\n{reference_data}"),
            ChatMessage(role="system", content=system_prompt)
        ]

        response = await llm.achat(messages)

        download_url, file_path = await run_blocking(create_ads_campaign_file, str(response))
        await ctx.store.set('campaign_ideas_file', file_path)

        return f"Google Ads Campaign ideas download URL: {download_url}. Next see if the user would like to select a campaign from the generated ideas and use the generate_search_campaign function to do this"
    except Exception as e:
        print(e)
        return f"Error generating campaign ideas: {e}"


async def generate_search_campaign(ctx: Context, selected_campaign: str) -> str:
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

        credentials = {
            "developer_token": DEVELOPER_TOKEN,
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "use_proto_plus": True,
            "login_customer_id": MANAGER_ID,
        }
        client = GoogleAdsClient.load_from_dict(credentials)

        ideas_file = await ctx.store.get('campaign_ideas_file', '')
        if not ideas_file:
            return "You must generate a campaign ideas file before creating a campaign."

        idea_text = await run_blocking(file_to_text, ideas_file)

        if selected_campaign.lower() not in idea_text.lower():
            return "Campaign idea not found in file."

        selected_block = ""
        for block in idea_text.split("---"):
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

        # mutate_campaign_budgets is blocking (network) -> run in executor
        budget_response = await run_blocking(
            budget_service.mutate_campaign_budgets,
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
        campaign.name = f"AI Generated – {selected_campaign} - {int(time.time())}"
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

        # Create campaign (network RPC) -> run in executor
        campaign_response = await run_blocking(
            campaign_service.mutate_campaigns,
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

        ad_group_response = await run_blocking(
            ad_group_service.mutate_ad_groups,
            customer_id=customer_id,
            operations=[ad_group_operation],
        )
        ad_group_resource = ad_group_response.results[0].resource_name

        # ---------------------------------------------------------------------
        # 5. Add Keywords
        # ---------------------------------------------------------------------
        keyword_service = client.get_service("AdGroupCriterionService")

        # Extract keywords, headlines and descriptions
        keywords = []
        keyword_cpcs = []
        headlines = []
        descriptions = []

        after_keywords = selected_block.split("Keywords:", 1)[1]
        keywords_section = after_keywords.split("Headlines:", 1)[0]

        after_headlines = selected_block.split("Headlines:", 1)[1]
        headlines_section = after_headlines.split("Descriptions:", 1)[0]

        after_descriptions = selected_block.split("Descriptions:", 1)[1]
        descriptions_section = after_descriptions.split("Final URL:", 1)[0]

        for line in keywords_section.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Remove leading "- " if present
            if line.startswith("- "):
                line = line[2:]

            if "{" in line and "}" in line:
                keyword = line.split("{")[0].strip()
                cpc = line.split("{")[1].replace("}", "").strip()
                # ensure int
                try:
                    cpc_int = int(cpc)
                except Exception:
                    cpc_int = 1_500_000
                keywords.append(keyword)
                keyword_cpcs.append(cpc_int)

        # Parse headlines
        for line in headlines_section.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("- "):
                line = line[2:]
            headlines.append(line)

        # Parse descriptions
        for line in descriptions_section.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("- "):
                line = line[2:]
            descriptions.append(line)


        # If no keywords were found, fallback to the campaign name (Not ideal, shouldn't happen)
        if not keywords:
            keywords = [selected_campaign]
            keyword_cpcs = [1_500_000]

        # Add keywords to the ad group (this is a network call)
        keyword_ops = []
        BILLABLE_UNIT = 10000

        for kw, cpc in zip(keywords, keyword_cpcs):
            op = client.get_type("AdGroupCriterionOperation")
            criterion = op.create
            criterion.ad_group = ad_group_resource
            criterion.keyword.text = kw
            criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum.EXACT
            criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED

            rounded_cpc = (cpc // BILLABLE_UNIT) * BILLABLE_UNIT
            criterion.cpc_bid_micros = rounded_cpc

            keyword_ops.append(op)

        await run_blocking(
            keyword_service.mutate_ad_group_criteria,
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

        ad = client.get_type("Ad")
        ad.final_urls.append("https://example.com")

        rsa = client.get_type("ResponsiveSearchAdInfo")

        # Properly create AdTextAsset objects
        for headline in headlines:
            asset = client.get_type("AdTextAsset")
            asset.text = sanitize_text(headline)
            rsa.headlines.append(asset)

        for description in descriptions:
            asset = client.get_type("AdTextAsset")
            asset.text = sanitize_text(description)
            rsa.descriptions.append(asset)

        ad.responsive_search_ad = rsa
        ad_group_ad.ad = ad

        # mutate_ad_group_ads is network blocking -> run in executor
        await run_blocking(
            ad_service.mutate_ad_group_ads,
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
