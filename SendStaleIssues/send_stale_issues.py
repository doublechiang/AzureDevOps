
import requests
from requests.auth import HTTPBasicAuth
import requests
import datetime
import pandas as pd
from msal import ConfidentialClientApplication
import os




# 設定參數
ORG = "quanta01"
PROJECT = "QCIDiag"
QUERY_ID = "9189e403-c093-4f64-b4c9-23021e295805" # 在網址列可以看到的一串英文數字
AZURE_PAT = os.environ.get("AZURE_PAT") # 從環境變數讀取 PAT，確保安全性

# Microsoft Graph API 的認證參數 (需要先在 Azure AD 註冊一個 App 並給予適當權限，然後把下面的參數換成你的 App 註冊資訊)
CLIENT_ID = os.environ.get('CLIENT_ID')
TENANT_ID = os.environ.get('TENANT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
SENDER_EMAIL = "chun-yu.chiang@quantatw.com" # 發件人

def generate_email_html(work_items_list, org_name, project_name):
    # 1. 統計數據
    total_count = len(work_items_list)
    p1_count = sum(1 for item in work_items_list if str(item.get('Priority')) == '1')
    p2_count = sum(1 for item in work_items_list if str(item.get('Priority')) == '2')
        
    table_rows = ""
    for item in work_items_list:
        wid = item.get('Id')
        title = item.get('Title')
        wi_type = item.get('Type', 'Task')
        assigned_to = item.get('AssignedTo', 'Unassigned')
        state = item.get('State', 'Proposed')
        priority = str(item.get('Priority', '2'))
        changed_date = item.get('ChangedDate', '')[:10]
        
        # 根據 Priority 決定文字顏色（配合緊湊型，拿掉大面積背景色以免刺眼）
        if priority == '1':
            p_style = "color: #dc2626; font-weight: bold;"
        elif priority == '2':
            p_style = "color: #b45309; font-weight: bold;" # 稍微加深一點點的橘色
        else:
            p_style = "color: #4b5563;"

        item_url = f"https://dev.azure.com/{org_name}/{project_name}/_workitems/edit/{wid}"
        
        # 這裡將 padding 縮小到 5px，並加入對齊原生表格的樣式
        table_rows += f"""
        <tr style="background-color: #ffffff;">
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: left;"><a href="{item_url}" style="color: #2563eb; text-decoration: underline;">{wid}</a></td>
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: left;">{title}</td>
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: left;">{wi_type}</td>
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: left;">{assigned_to}</td>
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: left;">{state}</td>
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: center; {p_style}">{priority}</td>
            <td style="padding: 5px 8px; border: 1px solid #d1d5db; text-align: left; color: #4b5563;">{changed_date}</td>
        </tr>
        """

    # 3. 完整的 HTML 模板 (針對表格外觀做了完全擬真調整)
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 15px; color: #212529;">
        <div style="max-width: 100%; margin: 0 auto; background: #ffffff;">
            
            <div style="padding-bottom: 12px; margin-bottom: 15px; border-bottom: 1px solid #dee2e6;">
                <h3 style="margin: 0 0 5px 0; color: #212529; font-size: 18px;">⚠️ 團隊待處理 Work Items 每日彙報</h3>
                <p style="margin: 0; font-size: 13px; color: #6c757d;">此報告列出符合逾期定義之項目 (P1>1天, P2>3天, P3>14天)。請同仁撥空更新狀態。</p>
            </div>
            
        <table style="width: 100%; border-collapse: separate; border-spacing: 10px; margin-bottom: 15px; table-layout: fixed;">
            <tr>
                <td style="background: #f8f9fa; padding: 10px; border-radius: 4px; border: 1px solid #dee2e6; text-align: center; vertical-align: middle;">
                    <div style="font-size: 12px; color: #6c757d; margin-bottom: 2px;">Total Issues</div>
                    <div style="font-size: 18px; font-weight: bold; color: #212529;">{total_count} <span style="font-size: 12px; font-weight: normal; color: #6c757d;">件</span></div>
                </td>
                
                <td style="background: #fff5f5; padding: 10px; border-radius: 4px; border: 1px solid #ffe3e3; text-align: center; vertical-align: middle;">
                    <div style="font-size: 12px; color: #c92a2a; margin-bottom: 2px;">🚨 高急迫性 (P1)</div>
                    <div style="font-size: 18px; font-weight: bold; color: #c92a2a;">{p1_count} <span style="font-size: 12px; font-weight: normal; color: #c92a2a;">件</span></div>
                </td>
                
                <td style="background: #fff9db; padding: 10px; border-radius: 4px; border: 1px solid #fff3bf; text-align: center; vertical-align: middle;">
                    <div style="font-size: 12px; color: #f08c00; margin-bottom: 2px;">⏳ 次高急迫 (P2)</div>
                    <div style="font-size: 18px; font-weight: bold; color: #f08c00;">{p2_count} <span style="font-size: 12px; font-weight: normal; color: #f08c00;">件</span></div>
                </td>
            </tr>
        </table>

            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-size: 13px; font-family: inherit;">
                    <thead>
                        <tr style="background-color: #107c41; color: #ffffff;"> <tr style="background-color: #0078d4; color: #ffffff; font-weight: 600;">
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: left; width: 60px;">ID</th>
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: left;">Title</th>
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: left; width: 110px;">Work Item Type</th>
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: left; width: 140px;">Assigned To</th>
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: left; width: 90px;">State</th>
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: center; width: 60px;">Priority</th>
                            <th style="padding: 6px 8px; border: 1px solid #005a9e; text-align: left; width: 100px;">Changed Date</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows}
                    </tbody>
                </table>
            </div>
            
            <div style="margin-top: 20px; text-align: left;">
                <p style="font-size: 13px;">Query: <a href="https://dev.azure.com/{org_name}/{project_name}/_dashboards" style="color: #2563eb; font-weight: bold; text-decoration: underline;">MSFT Open Items</a></p>
                <p style="color: #868e96; font-size: 11px; margin-top: 15px;">此郵件由自動化腳本發送，請勿直接回信。</p>
            </div>
            
        </div>
    </body>
    </html>
    """
    return html_content
    
def get_team_members(org, project, team_name, pat):
    # 注意：team_name 有時候是顯示名稱，如果是預設團隊通常跟專案同名
    url = f"https://dev.azure.com/{org}/_apis/projects/{project}/teams/{team_name}/members?api-version=6.0"
    response = requests.get(url, auth=HTTPBasicAuth('', pat))
    members = response.json().get('value', [])
    
    # 提取所有成員的 Email (通常在 identity['uniqueName'] 或 identity['properties']['Account'])
    emails = []
    for member in members:
        identity = member.get('identity', {})
        email = identity.get('uniqueName') # 大多數情況 uniqueName 就是 Email
        if email and "@" in email:
            emails.append(email)
    return emails

def send_email(to_emails, subject, html_body):
    # 這裡你可以使用任何郵件發送庫，例如 smtplib 或第三方服務
    print(f"發送郵件給: {', '.join(to_emails)}")
    print(f"主題: {subject}")
    app = ConfidentialClientApplication(
        CLIENT_ID, 
        authority=f"https://login.microsoftonline.com/{TENANT_ID}", 
        client_credential=CLIENT_SECRET
    )
    token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" in token:
            # 指定發件人 (例如你自己或是某個 Service Account)
            endpoint = f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/sendMail"
            
            email_payload = {
                "message": {
                    "subject": "🚨 Azure DevOps 逾期 Issue 每日彙報",
                    "body": {
                        "contentType": "HTML",
                        "content": html_content
                    },
                    "toRecipients": [{"emailAddress": {"address": email}} for email in to_emails],
                    # 如果要加抄送
                    # "ccRecipients": [{"emailAddress": {"address": "manager@company.com"}}]
                }
            }
            
            response = requests.post(
                endpoint, 
                json=email_payload, 
                headers={'Authorization': 'Bearer ' + token['access_token']}
            )
            
            if response.status_code == 202:
                print("郵件已成功寄出！")
            else:
                print(f"發信失敗: {response.text}")



url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit/wiql/{QUERY_ID}?api-version=6.0"

# 1. 執行 Query 取得 ID 清單
response = requests.get(url, auth=HTTPBasicAuth('', AZURE_PAT))
work_items = response.json().get('workItems', [])

if work_items:
    # 2. 批量取得 Work Item 詳細內容 (記得把需要的 fields 帶齊)
    # 這裡我把 System.WorkItemType, System.AssignedTo, System.State 都加進 fields 參數中
    fields_param = "System.Id,System.Title,System.WorkItemType,System.AssignedTo,System.State,Microsoft.VSTS.Common.Priority,System.ChangedDate"
    
    ids = ",".join([str(item['id']) for item in work_items])
    detail_url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit/workitems?ids={ids}&fields={fields_param}&api-version=6.0"
    details = requests.get(detail_url, auth=HTTPBasicAuth('', AZURE_PAT)).json()
    
    raw_list = details.get('value', [])
    
    # 💡 核心轉換步驟：把 ADO 複雜的 fields 扁平化，轉成乾淨的 list
    cleaned_work_items = []
    for item in raw_list:
        fields = item.get('fields', {})
        
        # 處理 Assigned To 可能為空（Unassigned）的情況
        assigned_obj = fields.get('System.AssignedTo', {})
        # 如果是物件就拿 displayName，否則（例如為空）就顯示 Unassigned
        assigned_name = assigned_obj.get('displayName', 'Unassigned') if isinstance(assigned_obj, dict) else 'Unassigned'
        
        cleaned_item = {
            'Id': item.get('id'),
            'Title': fields.get('System.Title', 'No Title'),
            'Type': fields.get('System.WorkItemType', 'Task'),
            'AssignedTo': assigned_name,
            'State': fields.get('System.State', 'New'),
            'Priority': fields.get('Microsoft.VSTS.Common.Priority', 4),
            'ChangedDate': fields.get('System.ChangedDate', '')
        }
        cleaned_work_items.append(cleaned_item)
        
    # 3. 將整理好的乾淨清單丟進 HTML 產生器
    html_content = generate_email_html(cleaned_work_items, ORG, PROJECT)
    send_email(["chun-yu.chiang@quantatw.com"], "Azure DevOps 逾期 Issue 彙報", html_content)




# 使用範例
# team_emails = get_team_members(ORG, PROJECT, "Msft", PAT)
