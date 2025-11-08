from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceNotFoundError
from azure.core.credentials import AzureSasCredential
import os
import asyncio

ACCOUNT_NAME = os.getenv("AZURE_ACCOUNT_NAME", "")
SAS_TOKEN = os.getenv("AZURE_SAS_TOKEN", "")
TABLE_NAME = "AdsManagerUsers"

def _store_user_data(user_id: str, google_creds: dict):
    try:
        # Unpack google credentials
        access_token = google_creds.get("access_token", "")
        refresh_token = google_creds.get("refresh_token", "")
        customer_id = google_creds.get("customer_id", "")

        # Authenticate the service
        credential = AzureSasCredential(SAS_TOKEN)
        service = TableServiceClient(
            endpoint=f"https://{ACCOUNT_NAME}.table.core.windows.net",
            credential=credential
        )

        # Get table client
        table_client = service.create_table_if_not_exists(table_name=TABLE_NAME)

        # Insert or update user credentials
        entity = {
            "PartitionKey": "UserData",
            "RowKey": user_id,
            "GoogleCustomerID": customer_id,
            "GoogleAccessToken": access_token,
            "GoogleRefreshToken": refresh_token
        }
        table_client.upsert_entity(entity=entity, mode=UpdateMode.MERGE)

        return True
    
    except Exception as e:
        print(f"Azure tables error: {e}")
        return False
    

def _get_user_data(user_id: str):
    # Authenticate the service
    credential = AzureSasCredential(SAS_TOKEN)
    service = TableServiceClient(
        endpoint=f"https://{ACCOUNT_NAME}.table.core.windows.net",
        credential=credential
    )

    # Get table client
    table_client = service.create_table_if_not_exists(table_name=TABLE_NAME)
    try:
        entity = table_client.get_entity(partition_key="UserData", row_key=user_id)
        customer_id = entity.get("GoogleCustomerID", "")
        access_token = entity.get("GoogleAccessToken", "")
        refresh_token = entity.get("GoogleRefreshToken", "")
        return customer_id, access_token, refresh_token
    except ResourceNotFoundError:
        print(f"User: {user_id} does not exist in table.")
        return None, None, None
    

async def store_user_data(user_id: str, google_creds: dict):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _store_user_data, user_id, google_creds)


async def get_user_data(user_id: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_user_data, user_id)