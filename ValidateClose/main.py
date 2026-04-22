import os
import requests
from flask import Flask, request
import sys
import urllib.parse
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s', flush=True)
logger=logging.getLogger(__name__)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

GUID_CACHE = {}

app = Flask(__name__)

# 從環境變數讀取 PAT (安全性考量)
PAT = os.environ.get("AZURE_PAT")
ORG_NAME = "quanta01" 

Area_Manager = {
    r"QCIDiag\QCT" : "EasonLin@quantatw.com",
    # r"QCIDiag\Amazon" : "Joe_Huang@quantatw.com",
    # r"QCIDiag\Google" : "Alex.Lee@quantatw.com",
    # r"QCIDiag\Meta" : "Lance.Wu@quantatw.com",
    r"QCIDiag\Msft" : "Wei-Kai.Huang@quantatw.com",
    r"TE_Test" : "chun-yu.chiang@quantatw.com",
}

My_Email = "chun-yu.chiang@quantatw.com"

def get_identify_by_email(email, auth):
    """透過 Email 查詢 Azure DevOps 內部的 GUID"""
    if email in GUID_CACHE:
        return GUID_CACHE[email]
    try:
        url = f"https://vssps.dev.azure.com/{ORG_NAME}/_apis/identities?searchFilter=General&filterValue={email}&api-version=7.1"
        response = requests.get(url, auth=auth)
        if response.status_code == 200:
            data = response.json()
            if data['count'] > 0:
                ident = data['value'][0]
                guid = ident['id']
                display_name = ident.get('providerDisplayName') or ident.get('displayName') or email.split('@')[0]
                # print(f"DEBUG: Full Identity {ident}", flush=True)
                GUID_CACHE[email] = (guid, display_name)
                return guid, display_name
    except Exception as e:
        print(f"Error fetching GUID for {email}: {e}")
    return None, None
    

@app.route("/", methods=["POST"])
def check_issue_status():
    try:

        payload = request.json
        # print((f"Payload: {payload}"))
        if not payload or 'resource' not in payload:
            return "Invalid Payload", 400

        # get the work item state is changed from old value to new value, if we can get the newValue, then it is a state change
        resource = payload.get('resource', {})
        fields = resource.get('fields', {})
        logger.debug(f"Received workitem update for {resource['workItemId']}")

        # if it's feature or epic type, then ignore it
        work_item_type = fields.get('System.WorkItemType', {})
        if work_item_type not in ['Feature', 'Epic']:
            return f"Ignore Item Type {work_item_type}", 200
            
        state_field = fields.get('System.State', {})
        new_state = state_field.get('newValue') if isinstance(state_field, dict) else None
        work_item_id = resource.get('workItemId') or resource.get('id')

        if new_state not in ['Closed', 'Done']:
            return f"Ignore Item State {new_state}", 200
                
        # 建立認證
        auth = ('', PAT)
        headers = {'Content-Type': 'application/json-patch+json'}

        # 1. 取得 Work Item 詳細資料 (包含 Relations)
        wi_url = f"https://dev.azure.com/{ORG_NAME}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=7.1"
        wi_response = requests.get(wi_url, auth=auth)
        # print(f"DEBUG: status code = {wi_response.status_code}", flush=True)
        if wi_response.status_code != 200:
            logger.error(f"Failed to get Work Item details {work_item_id}")
            return "Failed to get Work Item details", 200
        wi_full = wi_response.json()
        wi_fields = wi_full.get('fields', {})
        area_path = wi_fields.get('System.AreaPath', '')

        # Check only specific area path
        if area_path not in Area_Manager:
            logger.debug(f"Ignore Item Area Path {area_path}")
            return "Ignore Item Area Path", 200

        # 這裡檢查是誰更改的，避免無窮迴圈 (如果是自動化帳號改的就跳過)
        assigned_to = wi_fields.get('System.AssignedTo', {})
        owner_email = assigned_to.get('uniqueName', '').lower()
        changed_by = wi_fields.get('System.ChangedBy')
        if isinstance(changed_by, str):
            if '<' in changed_by:
                changed_by = changed_by.split('<')[1].split('>')[0]
            else:
                changed_by = changed_by.lower()
        else: 
            changed_by = changed_by.get('uniqueName', '').lower().strip()
        
        relations = wi_full.get('relations', [])

        reasons = []

        # If there is a PR, check if it is completed
        pr_completed = False

        pr_links = [r['url'] for r in relations if 'PullRequestId' in r['url']]
        for pr_url in pr_links:
            parsed_url = urllib.parse.unquote(pr_url)
            pr_id = parsed_url.split('/')[-1]
            pr_api = f"https://dev.azure.com/{ORG_NAME}/_apis/git/pullrequests/{pr_id}?api-version=7.1"
            pr_data = requests.get(pr_api, auth=auth).json()
            pr_status = pr_data['status']
            if pr_status == 'active':
                # if the PR is active, then we can not close the issue
                reasons.append(f"Active PR found, You need to close PR {pr_id} then close issue.")  
                break
            if pr_status == 'completed':
                pr_completed = True

        # if there is any completed PR, then check if there is a parent feature
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
            error_msg = " | ".join(reasons)

            to_mails = {owner_email.lower(), changed_by.lower()}
            cc_mails = {My_Email.lower()}
            if area_path in Area_Manager:
                cc_mails.add(Area_Manager[area_path].lower())
            cc_mails = cc_mails - to_mails

            def build_mention_tags(mails):
                tags = []
                for m in mails:
                    guid, display_name = get_identify_by_email(m, auth)
                    if guid is not None:
                        tag = f'<a href="mailto:{m}" data-vss-mention="version:2.0,guid:{guid}">@{display_name}</a>'
                        tags.append(tag)
                    else:
                        tags.append(f'<a href="mailto:{m}">@{m}</a>')
                return " ".join(tags)
                
            to_mentions_text = build_mention_tags(to_mails)
            cc_mentions_text = build_mention_tags(cc_mails)

            revert_body = [
                {"op": "add", "path": "/fields/System.State", "value": "In Progress"},
                {"op": "add", "path": "/fields/System.History", "value": f"<div>{to_mentions_text}<br>❌ <b>Auto Check Failed</b>: {error_msg}<br>{cc_mentions_text}</div>"}
            ]
            requests.patch(wi_url, json=revert_body, auth=auth, headers=headers)
            logger.debug("Policy Violated - Work Item Reverted")
            return "Policy Violated - Work Item Reverted", 200

        logger.debug("Policy Passed")
        return "Policy Passed", 200
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return "Error processing request", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))