import asyncio
import aiohttp
import functools
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.v22.common.types import AdTextAsset
from google.ads.googleads.v22.enums.types import AdGroupAdStatusEnum
from google.protobuf.field_mask_pb2 import FieldMask
from llama_index.core.workflow import Context
from llama_index.core.llms import ChatMessage
from helpers.file_helpers import create_keyword_report_file, file_to_text, create_ads_campaign_file, sanitize_text, text_to_file
from . import core
import os
import re
import time
import base64


DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
MANAGER_ID = os.getenv("GOOGLE_ADS_MANAGER_ID")

APP_URL = os.getenv("APP_URL", "")

async def get_google_client(ctx: Context):
    refresh_token = await ctx.store.get("google_refresh_token", "")
    customer_id = await ctx.store.get('google_customer_id', "")
    user_id = await ctx.store.get("user_id", "")

    if not refresh_token or not customer_id:
        url_safe_id = base64.urlsafe_b64encode(user_id.encode()).decode()
        return f"To use this tool, the user must authenticate via this link: {APP_URL}/authenticate?userId={url_safe_id}"

    credentials = {
        "developer_token": DEVELOPER_TOKEN,
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "use_proto_plus": True,
        "login_customer_id": MANAGER_ID
    }

    return GoogleAdsClient.load_from_dict(credentials)


async def run_blocking(func, *args, **kwargs):
    """
    Run a blocking function in the default ThreadPoolExecutor and return result.
    Use this to wrap any blocking I/O, network calls that are not async, CPU heavy tasks, etc.
    """
    loop = asyncio.get_running_loop()
    partial = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, partial)


async def get_data_from_urls(ctx: Context, urls: list) -> str:
    """Reads raw text data from URLs and saves to the user's data store."""
    organized = ""
    try:
        user_id = await ctx.store.get("user_id")
        uploaded_files = await ctx.store.get("uploaded_files", [])

        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        data = await response.text()
                except Exception as inner_e:
                    print(f"Failed to fetch {url}: {inner_e}")
                    continue
                if not data:
                    continue
                # Prepare organized text
                wrapped = f"URL:\n{url}\n\nContent:\n{data}\n"
                organized += wrapped

                # Save to file
                local_path = text_to_file(user_id, wrapped, "url_data")

                # Track uploaded files
                uploaded_files.append(local_path)

        await ctx.store.set("uploaded_files", uploaded_files)
        return organized
    except Exception as e:
        print(f"Error in get_data_from_urls: {e}")
        return ""

async def google_ads_keyword_search(ctx: Context, keywords: list) -> str:
    """Conducts a Google Ads Keyword search and returns keyword stats, up to 2000 total results."""
    try:
        client = await get_google_client(ctx)
        if isinstance(client, str):
            return client
        
        customer_id = await ctx.store.get("google_customer_id", "")
        keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

        language_id = "1000"  # English
        language_resource_name = f"languageConstants/{language_id}"

        uk_geo_target_id = "2840"
        geo_target_resource_name = f"geoTargetConstants/{uk_geo_target_id}"

        results = []

        # Limit per keyword
        per_seed_limit = max(1, 2000 // len(keywords))

        for seed_word in keywords:
            request = client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = customer_id
            request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS
            request.language = language_resource_name
            request.geo_target_constants.append(geo_target_resource_name)
            request.keyword_seed.keywords.append(seed_word)

            response = await run_blocking(keyword_plan_idea_service.generate_keyword_ideas, request=request)
            ideas = list(response)

            # Apply limit safely, no index error
            limited_ideas = ideas[:per_seed_limit]

            for idea in limited_ideas:
                metrics = idea.keyword_idea_metrics
                results.append({
                    "keyword": idea.text,
                    "avg_monthly_searches": metrics.avg_monthly_searches,
                    "competition": metrics.competition.name,
                    "low_top_of_page_bid": metrics.low_top_of_page_bid_micros,
                    "high_top_of_page_bid": metrics.high_top_of_page_bid_micros,
                    "competition_index": metrics.competition_index,
                    "seed_word": seed_word
                })

            await asyncio.sleep(1)

        if not results:
            return "No keyword data found for the provided search terms."

        report_download_url, report_file_path = await create_keyword_report_file(results)
        await ctx.store.set('keywords_search_file', report_file_path)

        return (
            f"In-depth keyword statistics spreadsheet download URL:\n{report_download_url}\n\n"
            f"Keyword search data file path:\n{report_file_path}\n\n"
            f"Keyword search data:\n{str(results)}."
        )

    except Exception as e:
        print(e)
        return f"Error conducting keyword search: {str(e)}"


async def create_campaign_ideas_report(ctx: Context, additional_notes: str, n_ideas: int) -> str:
    '''
    Uses an LLM to generate n Google Ads Campaign ideas based on the given reference data. Keyword searches and user uploads are automatically given to the LLM.
    Use additional_notes for any requests the user has about the campaign idea generation or any additional instructions you have for the ideas generation AI.
    '''
    try:
        llm = await core.get_llm()

        reference_data_file_paths = []
        keywords_file = await ctx.store.get('keywords_search_file', '')
        uploaded_files = await ctx.store.get('uploaded_files', [])

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
            "YOU MUST STRICTLY ADHERE TO CHARACTER LIMITS FOR HEADLINES (MAX 28 chars) AND DESCRIPTIONS (MAX 80 chars)."
        )

        messages = [
            ChatMessage(role="user", content=f"Reference Data:\n\n{reference_data}"),
            ChatMessage(role="system", content=system_prompt)
        ]

        response = await llm.achat(messages)

        download_url, file_path = await run_blocking(create_ads_campaign_file, str(response))
        await ctx.store.set('campaign_ideas_file', file_path)

        return f"Google Ads Campaign ideas download URL for user: {download_url}.\n\nCampaign ideas file contents:\n{str(response)}\n\nNext see if the user would like to select a campaign from the generated ideas and use the generate_search_campaign function to do this."
    except Exception as e:
        print(e)
        return f"Error generating campaign ideas: {e}"


async def read_campaign_ideas_names(ctx: Context) -> list:
    try:
        file = await ctx.store.get("campaign_ideas_file")
        ideas = await run_blocking(file_to_text, file)
        ideas = ideas.split("\n")

        idea_titles = [line for line in ideas if "# Idea" in line]

        if idea_titles:
            return idea_titles
        else:
            return "There are no campaign ideas available."
    except Exception as e:
        return f"There was an error: {e}"


async def generate_search_campaign(ctx: Context, selected_campaign: str) -> str:
    """
    Creates a fully detailed Google Search campaign with:
    - Budget extracted from ideas file
    - Campaign settings
    - Ad group
    - Keywords
    - Negative Keywords
    - Responsive Search Ad
    """
    try:
        await asyncio.sleep(3)
        customer_id = await ctx.store.get("google_customer_id", "")
        client = await get_google_client(ctx)
        if isinstance(client, str):
            return client

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
        # 1. Extract Budget
        # ---------------------------------------------------------------------
        budget_match = re.search(
            r"Budget:\s*£?(\d+(?:\.\d+)?)(?:\s*/\s*day)?",
            selected_block,
            re.IGNORECASE,
        )
        budget_daily = float(budget_match.group(1)) if budget_match else 5.0
        budget_micros = int(budget_daily * 1_000_000)

        # ---------------------------------------------------------------------
        # 2. Create Campaign Budget
        # ---------------------------------------------------------------------
        budget_service = client.get_service("CampaignBudgetService")
        budget_operation = client.get_type("CampaignBudgetOperation")
        budget = budget_operation.create
        budget.name = f"AI Budget – {selected_campaign} – {int(time.time())}"
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.amount_micros = budget_micros

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
            client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )
        campaign.manual_cpc.enhanced_cpc_enabled = False
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign.network_settings.target_partner_search_network = False

        # Geo Targeting: UK
        geo_target_constant_service = client.get_service("GeoTargetConstantService")
        location_info = client.get_type("LocationInfo")
        location_info.geo_target_constant = geo_target_constant_service.geo_target_constant_path("2840")
        campaign.geo_target_type_setting.positive_geo_target_type = (
            client.enums.PositiveGeoTargetTypeEnum.PRESENCE_OR_INTEREST
        )

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
        # 5. Extract Keywords, Negative Keywords, Headlines, Descriptions, Final URL
        # ---------------------------------------------------------------------
        def extract_section(text, section_name, next_section_name=None):
            section = text.split(f"{section_name}:", 1)
            if len(section) < 2:
                return ""
            content = section[1]
            if next_section_name and next_section_name in content:
                content = content.split(next_section_name, 1)[0]
            return content.strip()

        keywords_section = extract_section(selected_block, "Keywords", "Negative Keywords")
        negative_section = extract_section(selected_block, "Negative Keywords", "Headlines")
        headlines_section = extract_section(selected_block, "Headlines", "Descriptions")
        descriptions_section = extract_section(selected_block, "Descriptions", "Final URL")
        final_url = extract_section(selected_block, "Final URL")

        # Parse lines
        def parse_lines(section):
            lines = []
            for line in section.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("- "):
                    line = line[2:]
                lines.append(line)
            return lines

        keywords = []
        keyword_cpcs = []
        for line in parse_lines(keywords_section):
            if "{" in line and "}" in line:
                kw = line.split("{")[0].strip()
                try:
                    cpc = int(line.split("{")[1].replace("}", "").strip())
                except Exception:
                    cpc = 1_500_000
                keywords.append(kw)
                keyword_cpcs.append(cpc)
            else:
                keywords.append(line)
                keyword_cpcs.append(1_500_000)

        negative_keywords = parse_lines(negative_section)
        headlines = parse_lines(headlines_section)
        descriptions = parse_lines(descriptions_section)

        if not keywords:
            keywords = [selected_campaign]
            keyword_cpcs = [1_500_000]

        # ---------------------------------------------------------------------
        # 6. Add Keywords to Ad Group
        # ---------------------------------------------------------------------
        keyword_service = client.get_service("AdGroupCriterionService")
        keyword_ops = []
        BILLABLE_UNIT = 10000
        for kw, cpc in zip(keywords, keyword_cpcs):
            op = client.get_type("AdGroupCriterionOperation")
            criterion = op.create
            criterion.ad_group = ad_group_resource
            criterion.keyword.text = kw
            criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum.EXACT
            criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            criterion.cpc_bid_micros = (cpc // BILLABLE_UNIT) * BILLABLE_UNIT
            keyword_ops.append(op)

        await run_blocking(
            keyword_service.mutate_ad_group_criteria,
            customer_id=customer_id,
            operations=keyword_ops,
        )

        # ---------------------------------------------------------------------
        # 7. Add Negative Keywords via Shared Set
        # ---------------------------------------------------------------------
        if negative_keywords:
            # Create shared set
            shared_set_service = client.get_service("SharedSetService")
            shared_set_op = client.get_type("SharedSetOperation")
            shared_set = shared_set_op.create
            shared_set.name = f"Negatives – {selected_campaign} – {int(time.time())}"
            shared_set.type_ = client.enums.SharedSetTypeEnum.NEGATIVE_KEYWORDS
            
            shared_set_resp = await run_blocking(
                shared_set_service.mutate_shared_sets,
                customer_id=customer_id,
                operations=[shared_set_op],
            )
            shared_set_resource = shared_set_resp.results[0].resource_name
            
            # Add negative keywords
            shared_criterion_service = client.get_service("SharedCriterionService")
            criterion_ops = []
            
            for neg_kw in negative_keywords:
                op = client.get_type("SharedCriterionOperation")
                crit = op.create
                crit.shared_set = shared_set_resource
                crit.keyword.text = neg_kw
                crit.keyword.match_type = client.enums.KeywordMatchTypeEnum.EXACT
                criterion_ops.append(op)
            
            if criterion_ops:
                await run_blocking(
                    shared_criterion_service.mutate_shared_criteria,
                    customer_id=customer_id,
                    operations=criterion_ops,
                )
            
            # Link to campaign
            campaign_shared_set_service = client.get_service("CampaignSharedSetService")
            cs_op = client.get_type("CampaignSharedSetOperation")
            cs = cs_op.create
            cs.campaign = campaign_resource
            cs.shared_set = shared_set_resource
            
            await run_blocking(
                campaign_shared_set_service.mutate_campaign_shared_sets,
                customer_id=customer_id,
                operations=[cs_op],
            )


        ad_service = client.get_service("AdGroupAdService")
        ad_operation = client.get_type("AdGroupAdOperation")
        ad_group_ad = ad_operation.create
        ad_group_ad.ad_group = ad_group_resource
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED

        ad = client.get_type("Ad")
        ad.final_urls.append(final_url)

        rsa = client.get_type("ResponsiveSearchAdInfo")
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

        await run_blocking(
            ad_service.mutate_ad_group_ads,
            customer_id=customer_id,
            operations=[ad_operation],
        )
        await asyncio.sleep(3)

        return (
            "Search campaign created successfully!\n"
            f"- Budget: £{budget_daily}/day\n"
            f"- Campaign: {campaign_resource}\n"
            f"- Ad Group: {ad_group_resource}\n"
            f"- Keywords: {', '.join(keywords)}\n"
            f"- Negative keywords: {', '.join(negative_keywords)}\n"
            "Ad is paused for review."
        )

    except Exception as e:
        print(e, flush=True)
        return f"Error creating search campaign: {e}"


async def get_all_google_ads_campaign_details(ctx: Context):
    """Fetch ALL campaigns along with their ad groups, ads, keywords, and budgets."""
    customer_id = await ctx.store.get("google_customer_id", "")

    client = await get_google_client(ctx)
    if isinstance(client, str):
        return client

    ga_service = client.get_service("GoogleAdsService")
    budget_service = client.get_service("CampaignBudgetService")

    def fetch_all_details_sync():
        results = {"campaigns": {}}

        # ----------------- 1. Campaigns + Ad Groups -----------------
        query_campaigns = """
            SELECT
                campaign.id,
                campaign.name,
                campaign.campaign_budget,
                campaign.status,
                campaign.serving_status,
                ad_group.id,
                ad_group.name,
                ad_group.status
            FROM ad_group
            WHERE campaign.status != 'REMOVED'
            ORDER BY campaign.id, ad_group.id
        """

        stream = ga_service.search_stream(customer_id=customer_id, query=query_campaigns)
        for batch in stream:
            for row in batch.results:
                c = row.campaign
                ag = row.ad_group
                campaign_id = c.id
                ag_id = ag.id

                # ----------------- Get actual budget amount -----------------
                budget_amount = None
                if c.campaign_budget:
                    budget_query = f"""
                        SELECT
                            campaign_budget.amount_micros
                        FROM campaign_budget
                        WHERE campaign_budget.resource_name = '{c.campaign_budget}'
                    """
                    budget_stream = ga_service.search_stream(customer_id=customer_id, query=budget_query)
                    for b_batch in budget_stream:
                        for b_row in b_batch.results:
                            budget_amount = b_row.campaign_budget.amount_micros / 1_000_000  # Convert to standard units (£/day)

                if campaign_id not in results["campaigns"]:
                    results["campaigns"][campaign_id] = {
                        "id": c.id,
                        "name": c.name,
                        "budget": budget_amount,
                        "status": getattr(c.status, "name", c.status),
                        "serving_status": getattr(c.serving_status, "name", c.serving_status),
                        "ad_groups": {}
                    }

                campaign = results["campaigns"][campaign_id]
                if ag_id not in campaign["ad_groups"]:
                    campaign["ad_groups"][ag_id] = {
                        "id": ag.id,
                        "name": ag.name,
                        "status": getattr(ag.status, "name", ag.status),
                        "ads": [],
                        "keywords": [],
                        "negative_keywords": []
                    }

        # ----------------- 2. Ads -----------------
        for campaign_id, campaign in results["campaigns"].items():
            for ag_id, ad_group in campaign["ad_groups"].items():
                query_ads = f"""
                    SELECT
                        ad_group_ad.ad.id,
                        ad_group_ad.status,
                        ad_group_ad.ad.final_urls,
                        ad_group_ad.ad.responsive_search_ad.headlines,
                        ad_group_ad.ad.responsive_search_ad.descriptions
                    FROM ad_group_ad
                    WHERE ad_group_ad.ad_group = 'customers/{customer_id}/adGroups/{ag_id}'
                """
                stream_ads = ga_service.search_stream(customer_id=customer_id, query=query_ads)
                for batch in stream_ads:
                    for row in batch.results:
                        ad_obj = row.ad_group_ad.ad
                        ad_group["ads"].append({
                            "id": ad_obj.id,
                            "status": getattr(row.ad_group_ad, "status", None),
                            "final_urls": list(ad_obj.final_urls),
                            "headlines": [h.text for h in getattr(ad_obj.responsive_search_ad, "headlines", [])],
                            "descriptions": [d.text for d in getattr(ad_obj.responsive_search_ad, "descriptions", [])],
                        })

        # ----------------- 3. Keywords -----------------
        for campaign_id, campaign in results["campaigns"].items():
            for ag_id, ad_group in campaign["ad_groups"].items():
                query_keywords = f"""
                    SELECT
                        ad_group_criterion.keyword.text,
                        ad_group_criterion.keyword.match_type,
                        ad_group_criterion.status,
                        ad_group_criterion.cpc_bid_micros
                    FROM ad_group_criterion
                    WHERE ad_group_criterion.type = KEYWORD
                      AND ad_group_criterion.ad_group = 'customers/{customer_id}/adGroups/{ag_id}'
                """
                stream_kw = ga_service.search_stream(customer_id=customer_id, query=query_keywords)
                for batch in stream_kw:
                    for row in batch.results:
                        kw = row.ad_group_criterion
                        ad_group["keywords"].append({
                            "text": kw.keyword.text,
                            "match_type": getattr(kw.keyword.match_type, "name", kw.keyword.match_type),
                            "status": getattr(kw, "status", None),
                            "cpc_bid_micros": kw.cpc_bid_micros,
                            "cpc_bid_gbp": kw.cpc_bid_micros / 1_000_000 if kw.cpc_bid_micros else None
                        })

        # ----------------- 4. Ad Group Negative Keywords -----------------
        for campaign_id, campaign in results["campaigns"].items():
            for ag_id, ad_group in campaign["ad_groups"].items():
                query_negative_kw = f"""
                    SELECT
                        ad_group_criterion.keyword.text,
                        ad_group_criterion.keyword.match_type,
                        ad_group_criterion.status
                    FROM ad_group_criterion
                    WHERE ad_group_criterion.type = KEYWORD
                      AND ad_group_criterion.negative = TRUE
                      AND ad_group_criterion.ad_group = 'customers/{customer_id}/adGroups/{ag_id}'
                """
                stream_neg_kw = ga_service.search_stream(customer_id=customer_id, query=query_negative_kw)
                for batch in stream_neg_kw:
                    for row in batch.results:
                        kw = row.ad_group_criterion
                        ad_group["negative_keywords"].append({
                            "text": kw.keyword.text,
                            "match_type": getattr(kw.keyword.match_type, "name", kw.keyword.match_type),
                            "status": getattr(kw, "status", None)
                        })

        # ----------------- 5. Campaign Negative Keywords -----------------
        for campaign_id, campaign in results["campaigns"].items():
            query_campaign_neg_kw = f"""
                SELECT
                    campaign_criterion.keyword.text,
                    campaign_criterion.keyword.match_type,
                    campaign_criterion.status
                FROM campaign_criterion
                WHERE campaign_criterion.type = KEYWORD
                  AND campaign_criterion.negative = TRUE
                  AND campaign_criterion.campaign = 'customers/{customer_id}/campaigns/{campaign_id}'
            """
            stream_campaign_neg_kw = ga_service.search_stream(customer_id=customer_id, query=query_campaign_neg_kw)
            for batch in stream_campaign_neg_kw:
                for row in batch.results:
                    kw = row.campaign_criterion
                    campaign.setdefault("negative_keywords", []).append({
                        "text": kw.keyword.text,
                        "match_type": getattr(kw.keyword.match_type, "name", kw.keyword.match_type),
                        "status": getattr(kw, "status", None)
                    })

        return results

    return await run_blocking(fetch_all_details_sync)


async def manage_ad_group_keywords(ctx: Context, ad_group_id: str, add_keywords: list, remove_keywords: list):
    """
    Add and/or remove keywords in a specific ad group.
    
    Parameters:
    - ad_group_id: str, the ID of the ad group
    - add_keywords: list of dicts [{"text": "keyword text", "match_type": "EXACT|PHRASE|BROAD", "cpc_bid_gbp": float}, ...]. Should be an empty list if none.
    - remove_keywords: list of strings (keyword texts to remove). Should be an empty list if none.
    
    Returns: dict with 'added' and 'removed' keyword resource names
    """
    add_keywords = add_keywords or []
    remove_keywords = remove_keywords or []

    customer_id = await ctx.store.get("google_customer_id", "")

    client = await get_google_client(ctx)
    # If client is a string, it means the user isn't authenticated, this gets returned to the LLM to inform the user
    if isinstance(client, str):
        return client
    ad_group_criterion_service = client.get_service("AdGroupCriterionService")
    ad_group_service = client.get_service("AdGroupService")

    def sync_manage_keywords():
        added = []
        removed = []

        # ---------- ADD KEYWORDS ----------
        for kw in add_keywords:
            operation = client.get_type("AdGroupCriterionOperation")
            criterion = operation.create
            criterion.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)
            criterion.keyword.text = kw["text"]
            match_type_str = kw.get("match_type", "BROAD").upper()
            criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, match_type_str)
            criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED

            # Optional CPC bid
            if kw.get("cpc_bid_gbp") is not None:
                criterion.cpc_bid_micros = int(kw["cpc_bid_gbp"] * 1_000_000)

            response = ad_group_criterion_service.mutate_ad_group_criteria(
                customer_id=customer_id, operations=[operation]
            )
            added.append(response.results[0].resource_name)

        # ---------- REMOVE KEYWORDS ----------
        if remove_keywords:
            # First fetch existing keywords in the ad group to find resource names
            query = f"""
                SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text
                FROM ad_group_criterion
                WHERE ad_group.id = {ad_group_id} AND ad_group_criterion.type = KEYWORD
            """
            ga_service = client.get_service("GoogleAdsService")
            existing_kw = {}
            stream = ga_service.search_stream(customer_id=customer_id, query=query)
            for batch in stream:
                for row in batch.results:
                    existing_kw[row.ad_group_criterion.keyword.text] = row.ad_group_criterion.criterion_id

            for text in remove_keywords:
                if text in existing_kw:
                    resource_name = ad_group_criterion_service.ad_group_criterion_path(
                        customer_id, ad_group_id, existing_kw[text]
                    )
                    op = client.get_type("AdGroupCriterionOperation")
                    op.remove = resource_name
                    response = ad_group_criterion_service.mutate_ad_group_criteria(
                        customer_id=customer_id, operations=[op]
                    )
                    removed.append(response.results[0].resource_name)

        return {"added": added, "removed": removed}

    return await run_blocking(sync_manage_keywords)


async def manage_ad_group_ads(ctx: Context, ad_group_id: str, create_ads: list, remove_ad_ids: list):
    """
    Create and/or remove ads in a specific ad group (Responsive Search Ads only).

    Parameters:
    - ad_group_id: str, the ID of the ad group
    - create_ads: list of dicts [
        {"headlines": [str], "descriptions": [str], "final_urls": [str]}, ...
      ]. Must have at least 3 headlines and 2 descriptions per ad.
      Can be empty if none.
    - remove_ad_ids: list of ad IDs (integers or strings) to remove. Can be empty if none.

    Returns: dict with 'created' and 'removed' ad resource names
    """
    create_ads = create_ads or []
    remove_ad_ids = remove_ad_ids or []

    customer_id = await ctx.store.get("google_customer_id", "")

    client = await get_google_client(ctx)
    # If client is a string, it means the user isn't authenticated, this gets returned to the LLM to inform the user
    if isinstance(client, str):
        return client
    ad_group_ad_service = client.get_service("AdGroupAdService")
    ad_group_service = client.get_service("AdGroupService")

    def sync_manage_ads():
        created = []
        removed = []

        # ---------- CREATE RSA ADS ----------
        for ad in create_ads:
            op = client.get_type("AdGroupAdOperation")
            ad_obj = op.create
            ad_obj.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)

            # Responsive Search Ad requires multiple headlines/descriptions
            headlines = ad.get("headlines", [])
            descriptions = ad.get("descriptions", [])
            final_urls = ad.get("final_urls", [])

            if len(headlines) < 3 or len(descriptions) < 2 or not final_urls:
                raise ValueError(
                    "Each Responsive Search Ad requires at least 3 headlines, "
                    "2 descriptions, and 1 final URL."
                )

            for h in headlines:
                ad_obj.ad.responsive_search_ad.headlines.append(AdTextAsset(text=h))
            for d in descriptions:
                ad_obj.ad.responsive_search_ad.descriptions.append(AdTextAsset(text=d))
            ad_obj.ad.final_urls.extend(final_urls)
            ad_obj.status = AdGroupAdStatusEnum.AdGroupAdStatus.PAUSED

            resp = ad_group_ad_service.mutate_ad_group_ads(
                customer_id=customer_id, operations=[op]
            )
            created.append(resp.results[0].resource_name)

        # ---------- REMOVE ADS ----------
        for ad_id in remove_ad_ids:
            resource_name = ad_group_ad_service.ad_group_ad_path(customer_id, ad_group_id, ad_id)
            op = client.get_type("AdGroupAdOperation")
            op.remove = resource_name
            resp = ad_group_ad_service.mutate_ad_group_ads(
                customer_id=customer_id, operations=[op]
            )
            removed.append(resp.results[0].resource_name)

        return {"created": created, "removed": removed}

    return await run_blocking(sync_manage_ads)


async def manage_ad_groups(ctx: Context, campaign_id: str, create_ad_groups:list, remove_ad_group_ids: list):
    """
    Create and/or remove ad groups in a specific campaign.

    Parameters:
    - campaign_id: str, the ID of the campaign
    - create_ad_groups: list of dicts [{"name": str, "status": "ENABLED|PAUSED"}, ...]. Should be an empty list if none.
    - remove_ad_group_ids: list of ad group IDs (integers or strings) to remove. Should be an empty list if none.

    Returns: dict with 'created' and 'removed' ad group resource names
    """
    create_ad_groups = create_ad_groups or []
    remove_ad_group_ids = remove_ad_group_ids or []

    customer_id = await ctx.store.get("google_customer_id", "")

    client = await get_google_client(ctx)
    # If client is a string, it means the user isn't authenticated, this gets returned to the LLM to inform the user
    if isinstance(client, str):
        return client
    ad_group_service = client.get_service("AdGroupService")

    def sync_manage_ad_groups():
        created = []
        removed = []

        # ---------- CREATE AD GROUPS ----------
        for ag in create_ad_groups:
            operation = client.get_type("AdGroupOperation")
            ad_group = operation.create
            ad_group.name = ag["name"]
            ad_group.campaign = ad_group_service.campaign_path(customer_id, campaign_id)
            status_str = ag.get("status", "ENABLED").upper()
            ad_group.status = getattr(client.enums.AdGroupStatusEnum, status_str)

            response = ad_group_service.mutate_ad_groups(
                customer_id=customer_id, operations=[operation]
            )
            created.append(response.results[0].resource_name)

        # ---------- REMOVE AD GROUPS ----------
        for ag_id in remove_ad_group_ids:
            resource_name = ad_group_service.ad_group_path(customer_id, ag_id)
            op = client.get_type("AdGroupOperation")
            op.remove = resource_name
            response = ad_group_service.mutate_ad_groups(
                customer_id=customer_id, operations=[op]
            )
            removed.append(response.results[0].resource_name)

        return {"created": created, "removed": removed}

    return await run_blocking(sync_manage_ad_groups)


async def adjust_campaign_budget(ctx: Context, campaign_id: str, new_budget: float) -> str:
    customer_id = await ctx.store.get("google_customer_id", "")

    client = await get_google_client(ctx)
    if isinstance(client, str):
        return client

    budget_micros = int(new_budget * 1_000_000)

    campaign_service = client.get_service("CampaignService")
    budget_service = client.get_service("CampaignBudgetService")

    try:
        query = f"""
            SELECT
                campaign.campaign_budget
            FROM campaign
            WHERE campaign.id = {campaign_id}
        """
        search_response = await run_blocking(
            client.get_service("GoogleAdsService").search,
            customer_id=customer_id,
            query=query,
        )

        rows = list(search_response)
        if not rows:
            return f"Campaign with ID {campaign_id} not found."

        budget_resource_name = rows[0].campaign.campaign_budget

        budget_operation = client.get_type("CampaignBudgetOperation")
        budget = budget_operation.update
        budget.resource_name = budget_resource_name
        budget.amount_micros = budget_micros

        mask = FieldMask(paths=["amount_micros"])
        client.copy_from(budget_operation.update_mask, mask)

        await run_blocking(
            budget_service.mutate_campaign_budgets,
            customer_id=customer_id,
            operations=[budget_operation],
        )

        return f"Budget updated to £{new_budget}/day."

    except Exception as e:
        return f"Error adjusting budget: {e}"