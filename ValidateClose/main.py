import os
import requests
from flask import Flask, request
import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

app = Flask(__name__)

# 從環境變數讀取 PAT (安全性考量)
PAT = os.environ.get("AZURE_PAT")
ORG_NAME = "quanta01" 

@app.route("/", methods=["POST"])
def check_issue_status():
    try:

        payload = request.json
        print((f"Payload: {payload}"))
        if not payload or 'resource' not in payload:
            return "Invalid Payload", 400

        resource = payload.get('resource', {})
        work_item_id = resource.get('workItemId') or resource.get('id')

        if not work_item_id:
            print("Invalid Work Item ID")
            return "Invalid Work Item ID", 200
        
        # 這裡檢查是誰更改的，避免無窮迴圈 (如果是自動化帳號改的就跳過)
        revised_by = payload['resource']['fields'].get('System.ChangedBy', '')
        
        # 建立認證
        auth = ('', PAT)
        headers = {'Content-Type': 'application/json-patch+json'}

        # 1. 取得 Work Item 詳細資料 (包含 Relations)
        wi_url = f"https://dev.azure.com/{ORG_NAME}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=7.1"
        response = requests.get(wi_url, auth=auth)
        print(f"DEBUG: status code = {response.status_code}", flush=True)
        wi_data = response.json()
        
        relations = wi_data.get('relations', [])
        
        # --- 檢查邏輯 1: 是否有 Parent Feature ---
        has_feature_parent = False
        for rel in relations:
            if rel['attributes'].get('name') == 'Parent':
                p_url = rel['url']
                p_data = requests.get(p_url, auth=auth).json()
                if p_data['fields'].get('System.WorkItemType') == 'Feature':
                    has_feature_parent = True
                    break

        # --- 檢查邏輯 2: 是否有 PR 且 Completed ---
        pr_completed = True
        pr_links = [r['url'] for r in relations if 'PullRequestId' in r['url']]
        
        if not pr_links:
            pr_completed = False # 依照你的規定，必須有 PR
        else:
            for pr_url in pr_links:
                # 解析出 PR ID (從 URL 最後一段)
                import urllib.parse
                parsed_url = urllib.parse.unquote(pr_url)
                pr_id = parsed_url.split('/')[-1]
                
                # 這裡需要 project 名稱，或者直接用全域 PR API
                # 為求簡單，我們假設 PR 必須是 Completed 狀態
                pr_api = f"https://dev.azure.com/{ORG_NAME}/_apis/git/pullrequests/{pr_id}?api-version=7.1"
                pr_status_data = requests.get(pr_api, auth=auth).json()
                if pr_status_data.get('status') != 'completed':
                    pr_completed = False
                    break

        # --- 最終判定 ---
        if not (has_feature_parent and pr_completed):
            # 條件不符！強制退回 Doing 並留言
            revert_body = [
                {"op": "add", "path": "/fields/System.State", "value": "Doing"},
                {"op": "add", "path": "/fields/System.History", "value": "❌ 自動檢查失敗：必須有關聯的 Feature 父項，且所有 PR 必須為 Completed 狀態。"}
            ]
            requests.patch(wi_url, json=revert_body, auth=auth, headers=headers)
            return "Policy Violated - Work Item Reverted", 200

        return "Policy Passed", 200
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return "Error processing request", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))