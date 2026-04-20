import os
import requests
from flask import Flask, request
import sys
import urllib.parse

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

app = Flask(__name__)

# 從環境變數讀取 PAT (安全性考量)
PAT = os.environ.get("AZURE_PAT")
ORG_NAME = "quanta01" 

Area_Manager = {
    "QCIDiag\QCT" : "EasonLin@quantatw.com",
    "QCIDiag\Amazon" : "Joe_Huang@quantatw.com",
    "QCIDiag\Google" : "Alex.Lee@quantatw.com",
    "QCIDiag\Meta" : "Lance.Wu@quantatw.com",
    "QCIDiag\Msft" : "Wei-Kai.Huang@quantatw.com",
}

My_Email = "chun-yu.chiang@quatatw.com"

@app.route("/", methods=["POST"])
def check_issue_status():
    try:

        payload = request.json
        print((f"Payload: {payload}"))
        if not payload or 'resource' not in payload:
            return "Invalid Payload", 400

        resource = payload.get('resource', {})
        work_item_id = resource.get('workItemId') or resource.get('id')
        new_state = resource.get('fields', {}).get('System.State')

        if not work_item_id:
            print("Invalid Work Item ID")
            return "Invalid Work Item ID", 200

        if new_state not in ['Closed', 'Done']:
            return "Work Item State is not Closed or Done", 200
        
        # 這裡檢查是誰更改的，避免無窮迴圈 (如果是自動化帳號改的就跳過)
        revised_by = payload['resource']['fields'].get('System.ChangedBy', '')
        
        # 建立認證
        auth = ('', PAT)
        headers = {'Content-Type': 'application/json-patch+json'}

        # 1. 取得 Work Item 詳細資料 (包含 Relations)
        wi_url = f"https://dev.azure.com/{ORG_NAME}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=7.1"
        wi_response = requests.get(wi_url, auth=auth)
        print(f"DEBUG: status code = {wi_response.status_code}", flush=True)
        if wi_response.status_code != 200:
            return "Failed to get Work Item details", 200
        wi_full = wi_response.json()
        wi_fields = wi_full.get('fields', {})
        area_path = wi_fields.get('System.AreaPath', '')
        assigned_to = wi_fields.get('System.AssignedTo', {})
        owner_email = assigned_to.get('uniqueName', '')
        
        relations = wi_full.get('relations', [])


        reasons = []
        

        # If there is a PR, check if it is completed
        active_pr_found = False
        pr_completed = False

        pr_links = [r['url'] for r in relations if 'PullRequestId' in r['url']]
        for pr_url in pr_links:
            parsed_url = urllib.parse.unquote(pr_url)
            pr_id = parsed_url.split('/')[-1]
            pr_api = f"https://dev.azure.com/{ORG_NAME}/_apis/git/pullrequests/{pr_id}?api-version=7.1"
            pr_data = requests.get(pr_api, auth=auth).json()
            pr_status = pr_data['status']
            if pr_status == 'active':
                active_pr_found = True
                reasons.append(f"Active PR found, You need to close PR then close issue: {pr_id}")  
                break
            if pr_status == 'completed':
                pr_completed = True

        # if there is a completed PR, then check if there is a parent feature
        if pr_completed:
            has_feature_parent = False
            for rel in relations:
                if rel['attributes'].get('name') == 'Parent':
                    p_url = rel['url']
                    p_data = requests.get(p_url, auth=auth).json()
                    if p_data['fields'].get('System.WorkItemType') == 'Feature':
                        has_feature_parent = True
                        break
            if not has_feature_parent:
                reasons.append("No Feature parent issue link")

        if reasons:
            error_msg = "\n".join(reasons)
            mentions = f"@{owner_email}"
            if area_path in Area_Manager:
                mentions += f" @{Area_Manager[area_path]}"
            mentions += f" @{My_Email}"

            revert_body = [
                {"op": "add", "path": "/fields/System.State", "value": "In Progress"},
                {"op": "add", "path": "/fields/System.History", "value": f"❌ Auto Check Failed: {error_msg}\n{mentions}"}
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