from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceNotFoundError
from azure.core.credentials import AzureSasCredential
import os
import asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import base64


ACCOUNT_NAME = os.getenv("AZURE_ACCOUNT_NAME", "")
SAS_TOKEN = os.getenv("AZURE_SAS_TOKEN", "")
TABLE_NAME = "AdsManagerUsers"

AES_SECRET = os.getenv("AES_SECRET_KEY") # For encrypting/decrypting refresh token

def _store_user_data(user_id: str, google_creds: dict):
    try:
        # Unpack google credentials
        refresh_token = google_creds.get("refresh_token", "")
        refresh_token = encrypt_token(refresh_token)
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
        refresh_token = entity.get("GoogleRefreshToken", "")
        refresh_token = decrypt_token(refresh_token)

        return customer_id, refresh_token
    except ResourceNotFoundError:
        print(f"User: {user_id} does not exist in table.")
        return None, None
    

async def store_user_data(user_id: str, google_creds: dict):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _store_user_data, user_id, google_creds)


async def get_user_data(user_id: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_user_data, user_id)


def encrypt_token(token: str) -> str:
    aesgcm = AESGCM(base64.b64decode(AES_SECRET))
    nonce = os.urandom(12)
    encrypted = aesgcm.encrypt(nonce, token.encode("utf-8"), None)
    return base64.b64encode(nonce + encrypted).decode("utf-8")


def decrypt_token(enc_token: str) -> str:
    data = base64.b64decode(enc_token)
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(base64.b64decode(AES_SECRET))
    decrypted = aesgcm.decrypt(nonce, ciphertext, None)
    return decrypted.decode("utf-8")