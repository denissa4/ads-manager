import os
import re
import uuid
import asyncio
import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment
import pandas as pd
import docx
import pdfplumber
import aiohttp

APP_URL = os.getenv("APP_URL", "")
FILE_SERVE_DIR = '/var/www/html/bot/static/files'
USER_UPLOADS_DIR = '/app/user_uploads'

CLIENT_ID = os.getenv("MicrosoftAppId", "")
CLIENT_SECRET = os.getenv("MicrosoftAppPassword", "")
TENANT_ID = os.getenv("MicrosoftAppTenantId", "")


async def create_keyword_report_file(data: list) -> str:
    file_name = f"{str(uuid.uuid4())[:6]}_keyword_statistics.xlsx"
    file_path = f"{FILE_SERVE_DIR}/{file_name}"

    os.makedirs(FILE_SERVE_DIR, exist_ok=True)

    # Create a new workbook and active worksheet
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Keyword Statistics"

    # Define headers
    headers = [
        "Keyword",
        "Average Monthly Searches",
        "Competition",
        "Competition Index",
        "Low Top of Page Bid (micros)",
        "High Top of Page Bid (micros)"
    ]

    # Write header row with styling
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Write data rows
    for row_num, result in enumerate(data, 2):
        ws.cell(row=row_num, column=1, value=result.get("keyword", ""))
        ws.cell(row=row_num, column=2, value=result.get("avg_monthly_searches", "N/A"))
        ws.cell(row=row_num, column=3, value=result.get("competition", "N/A"))
        ws.cell(row=row_num, column=4, value=result.get("competition_index", "N/A"))
        ws.cell(row=row_num, column=5, value=result.get("low_top_of_page_bid", "N/A"))
        ws.cell(row=row_num, column=6, value=result.get("high_top_of_page_bid", "N/A"))

    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        column = col[0].column  # Get column name
        for cell in col:
            try:
                cell_length = len(str(cell.value))
                if cell_length > max_length:
                    max_length = cell_length
            except Exception:
                pass
        ws.column_dimensions[get_column_letter(column)].width = max_length + 2

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, wb.save, file_path)

    return f"{APP_URL}/downloads/{file_name}", file_path


def create_ads_campaign_file(data: str) -> str:
    file_name = f"{str(uuid.uuid4())[:6]}_ads_campaign_ideas.txt"
    file_path = f"{FILE_SERVE_DIR}/{file_name}"

    with open(file_path, 'w') as f:
        f.write(data)

    return f"{APP_URL}/downloads/{file_name}", file_path


def file_to_text(file_path: str) -> str:
    if file_path.endswith(('.xlsx', '.xls')):
        # Read Excel and convert to CSV text
        df = pd.read_excel(file_path)
        return df.to_csv(index=False)
    
    elif file_path.endswith('.csv'):
        df = pd.read_csv(file_path)
        return df.to_csv(index=False)
    
    elif file_path.endswith(('.txt', 'html', 'htm')):
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    elif file_path.endswith('.docx'):
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    
    elif file_path.endswith('.pdf'):
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        return text
    
    else:
        raise ValueError("Unsupported file type")


def text_to_file(user_id: str, text_data: str, filename: str) -> str:
    try:
        os.makedirs(f"{USER_UPLOADS_DIR}/{user_id}", exist_ok=True)

        file_uid = str(uuid.uuid4().hex[:6])
        local_path = f"{USER_UPLOADS_DIR}/{user_id}/{file_uid}_{filename}.txt"
        print(f"Local Path: {local_path}", flush=True)
        with open(local_path, 'w') as f:
            f.write(text_data)
        return local_path
    except:
        try:
            local_path = f"{USER_UPLOADS_DIR}/{user_id}/{file_uid}.txt"
            with open(local_path, 'w') as f:
                f.write(text_data)
            return local_path
        except Exception as e:
            raise e



def sanitize_text(text: str) -> str:
    # Remove prohibited symbols
    return re.sub(r"[#\$]{2,}", "", text).strip()


async def handle_attachments(user_id: str, attachment_urls: list):
    os.makedirs(f"{USER_UPLOADS_DIR}/{user_id}", exist_ok=True)

    results = []

    async with aiohttp.ClientSession() as session:
        for el in attachment_urls:
            url = el.get('url', '')
            filename = f"{uuid.uuid4().hex[:4]}_{el.get('name', 'unknown.txt')}"
            local_path = f"{USER_UPLOADS_DIR}/{user_id}/{filename}"

            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        results.append({
                            "filename": filename,
                            "file_path": None,
                            "text": f"[Failed to download, status: {resp.status}]"
                        })
                        continue

                    # Write file to user uploads directory
                    content = await resp.read()
                    with open(local_path, "wb") as f:
                        f.write(content)

                # Extract text content
                try:
                    text = file_to_text(local_path)
                    text = sanitize_text(text)
                except Exception as e:
                    text = f"[Error reading file: {e}]"

                results.append({
                    "filename": filename,
                    "file_path": local_path,
                    "text": text
                })

            except Exception as e:
                results.append({
                    "filename": filename,
                    "file_path": None,
                    "text": f"[Exception during download: {e}]"
                })

    return results
