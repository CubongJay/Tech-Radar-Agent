import json
import logging
import os
import re
import time
import uuid
from datetime import timedelta
import azure.durable_functions as df
import azure.functions as func
import requests
import logging
from azure.storage.blob import BlobServiceClient
from bs4 import BeautifulSoup
from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException
from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel
from langchain_core.messages import HumanMessage, SystemMessage

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="dashboard", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard(req: func.HttpRequest) -> func.HttpResponse:
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Tech Radar Agent</title>
        <!-- Load Marked.js for Markdown rendering -->
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <style>
            :root {
                --bg: #0f172a;
                --card-bg: #1e293b;
                --text: #f8fafc;
                --primary: #3b82f6;
                --primary-hover: #2563eb;
                --border: #334155;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 40px 20px;
                display: flex;
                justify-content: center;
            }
            .container { width: 100%; max-width: 800px; }
            h1 { text-align: center; margin-bottom: 30px; }
            .card {
                background-color: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            }
            input[type="text"] {
                width: 100%;
                padding: 12px;
                border-radius: 6px;
                border: 1px solid var(--border);
                background-color: var(--bg);
                color: var(--text);
                font-size: 16px;
                box-sizing: border-box;
                margin-bottom: 15px;
            }
            button {
                background-color: var(--primary);
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 6px;
                font-size: 16px;
                cursor: pointer;
                font-weight: 600;
                transition: background-color 0.2s;
            }
            button:hover { background-color: var(--primary-hover); }
            button:disabled { background-color: #475569; cursor: not-allowed; }
            
            #approval-section, #result-section { display: none; }
            
            .source-item {
                display: flex;
                align-items: flex-start;
                margin-bottom: 10px;
                padding: 10px;
                border: 1px solid var(--border);
                border-radius: 6px;
            }
            .source-item input[type="checkbox"] { margin-top: 4px; margin-right: 12px; cursor: pointer; }
            .source-title { font-weight: bold; margin-bottom: 4px; }
            .source-url { font-size: 12px; color: #94a3b8; word-break: break-all; }
            
            #markdown-output {
                background-color: var(--bg);
                padding: 20px;
                border-radius: 6px;
                border: 1px solid var(--border);
                overflow-x: auto;
                line-height: 1.6;
            }
            #markdown-output h1, #markdown-output h2, #markdown-output h3 { color: #60a5fa; }
            #markdown-output a { color: #38bdf8; }
            #markdown-output code { background-color: #334155; padding: 2px 4px; border-radius: 4px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🛰️ Tech Radar Agent</h1>
            
            <div class="card" id="input-section">
                <h2>What do you want to research?</h2>
                <input type="text" id="topic-input" placeholder="e.g., Azure Durable Functions Human Interaction Pattern">
                <button onclick="startResearch()" id="research-btn">Start Research</button>
                <p id="status-text" style="margin-top: 15px; color: #94a3b8;"></p>
            </div>

            <div class="card" id="approval-section">
                <h2>Review Sources</h2>
                <p>Select the sources you want the AI to read:</p>
                <div id="sources-list"></div>
                <button onclick="approveSources()" style="margin-top: 15px;">Approve Selected</button>
            </div>

            <div class="card" id="result-section">
                <h2>Research Report</h2>
                <div id="markdown-output"></div>
            </div>
        </div>

        <script>
            let currentStatusUrl = "";
            let currentEventUrl = "";
            let pollingInterval = null;

            async function startResearch() {
                const topic = document.getElementById("topic-input").value;
                if (!topic) return alert("Please enter a topic");

                document.getElementById("research-btn").disabled = true;
                document.getElementById("status-text").innerText = "Starting workflow...";

                try {
                    const response = await fetch("/api/research", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ topic: topic })
                    });

                    const data = await response.json();
                    currentStatusUrl = data.statusQueryGetUri;
                    currentEventUrl = data.sendEventPostUri;

                    document.getElementById("status-text").innerText = "Searching the web for sources...";
                    
                    // Start polling every 2 seconds
                    pollingInterval = setInterval(checkStatus, 2000);
                } catch (error) {
                    document.getElementById("status-text").innerText = "Error: " + error.message;
                    document.getElementById("research-btn").disabled = false;
                }
            }

            async function checkStatus() {
                try {
                    const response = await fetch(currentStatusUrl);
                    const data = await response.json();

                    if (data.runtimeStatus === "Running" && data.customStatus && data.customStatus.sources) {
                        // We have sources waiting for approval
                        document.getElementById("status-text").innerText = "Waiting for your approval...";
                        document.getElementById("input-section").style.display = "none";
                        document.getElementById("approval-section").style.display = "block";
                        
                        renderSources(data.customStatus.sources);
                        
                        // Stop polling while the user reviews the sources
                        clearInterval(pollingInterval); 
                    } 
                    else if (data.runtimeStatus === "Completed") {
                        // The workflow is done
                        clearInterval(pollingInterval);
                        document.getElementById("status-text").innerText = "Research complete!";
                        document.getElementById("approval-section").style.display = "none";
                        document.getElementById("result-section").style.display = "block";
                        
                        // Render the Markdown output
                        document.getElementById("markdown-output").innerHTML = marked.parse(data.output);
                    }
                    else if (data.runtimeStatus === "Failed") {
                        clearInterval(pollingInterval);
                        document.getElementById("status-text").innerText = "Workflow failed. Check the terminal logs.";
                    }
                } catch (error) {
                    console.error("Polling error:", error);
                }
            }

            function renderSources(sources) {
                const container = document.getElementById("sources-list");
                container.innerHTML = ""; // Clear previous
                
                sources.forEach((source, index) => {
                    const div = document.createElement("div");
                    div.className = "source-item";
                    
                    div.innerHTML = `
                        <input type="checkbox" id="source-${index}" value="${source.url}" checked>
                        <label for="source-${index}">
                            <div class="source-title">${source.title}</div>
                            <div class="source-url">${source.url}</div>
                        </label>
                    `;
                    container.appendChild(div);
                });
            }

            async function approveSources() {
                const checkboxes = document.querySelectorAll("#sources-list input[type='checkbox']:checked");
                const approvedUrls = Array.from(checkboxes).map(cb => cb.value);

                if (approvedUrls.length === 0) return alert("Please select at least one source.");

                document.getElementById("approval-section").style.display = "none";
                document.getElementById("status-text").innerText = "Scraping and summarizing...";
                document.getElementById("status-text").style.display = "block";

                // Build the event URL (replace {eventName} with ApprovedSources)
                const eventUrl = currentEventUrl.replace("{eventName}", "ApprovedSources");

                try {
                    await fetch(eventUrl, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(approvedUrls)
                    });

                    // Resume polling to wait for the final result
                    pollingInterval = setInterval(checkStatus, 2000);
                } catch (error) {
                    alert("Failed to approve: " + error.message);
                }
            }
        </script>
    </body>
    </html>
    """
    return func.HttpResponse(html_content, mimetype="text/html")


@app.route(route="research", methods=["POST"])
@app.durable_client_input(client_name="client")
async def start_research(req: func.HttpRequest, client) -> func.HttpResponse:
    try:
        req_body = req.get_json()
        topic = req_body.get("topic")

        if not topic:
            return func.HttpResponse(
                "Please pass a 'topic' in the request body", status_code=400
            )

        instance_id = await client.start_new("tech_radar_orchestrator", None, topic)
        return client.create_check_status_response(req, instance_id)
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400)


@app.activity_trigger(input_name="topic")
def search_activity(topic: str) -> list:
    """ """

    logging.info(f"Searching the web for: {topic}")
    results_list = []
    try:

        with DDGS() as ddgs:
            results = ddgs.text(topic, max_results=5)

            for r in results:
                results_list.append(
                    {
                        "title": r.get("title", "No Title"),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", "No snippet"),
                    }
                )
        print(f"Found {len(results_list)} results")
    except RatelimitException:
        time.sleep(3)

        with DDGS() as ddgs:
            results = ddgs.text(topic, max_results=5, backend="bing")

            for r in results:
                results_list.append(
                    {
                        "title": r.get("title", "No Title"),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", "No snippet"),
                    }
                )

    return results_list


@app.activity_trigger(input_name="url")
def scrape_activity(url: str) -> str:
    logging.info(f"`Scraping URL: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()
        text = soup.get_text(separator="\n")
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split(" "))
        clean_text = "\n".join(chunk for chunk in chunks if chunk)
        return clean_text[:10000]
    except Exception as e:
        print(f"Failed to scrape {url}: {str(e)}")
        return f"Error Scraping {url}: {str(e)}"


@app.activity_trigger(input_name="input_data")
def summarize_activity(input_data: dict):

    topic = input_data["topic"]
    scraped_texts = input_data["scraped_texts"]

    combined_text = "\n\n-----\n\n".join(scraped_texts)
    llm = AzureAIChatCompletionsModel(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        credential=os.getenv("AZURE_OPENAI_API_KEY"),
    )
    system_prompt = """
                    You are a assistant summary agent, your job is to take inputs of text, and then summarize it, and return a final report in md format.
                    Constraints:
                        Only use information from the provided text.

                    """
    user_prompt = f"Here is the research topic: {topic} \n\n `Here is the text to summarize: \n\n {combined_text}"

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    response = llm.invoke(messages)
    final_response = response.content

    safe_topic = re.sub(r"[^a-zA-Z0-9]+", "-", topic).strip("-").lower()
    short_id = str(uuid.uuid4())[:6]
    filename = f"{safe_topic}-{short_id}.md"

    conn_str = os.getenv("BLOB_CONNECTION_STRING")
    blob_storage_client = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_storage_client.get_container_client("summary")
    if not container_client:
        try:

            blob_storage_client.create_container("summary")
        except Exception:
            pass
    container_client = blob_storage_client.get_container_client("summary")
    blob_client = container_client.get_blob_client(filename)
    blob_client.upload_blob(final_response, overwrite=True)
    logging.info("File got created successfully")
    return f"File got created successfully, here is the url: summary/{filename}"


@app.orchestration_trigger(context_name="context")
def tech_radar_orchestrator(context: df.DurableOrchestrationContext):
    topic = context.get_input()
    search_results = yield context.call_activity("search_activity", topic)
    if not search_results:
        return f"No results found for this topic:{topic} "
    context.set_custom_status(
        {"message": "Waiting for human approval.", "sources": search_results}
    )
    timeout_seconds = 600
    deadline = context.current_utc_datetime + timedelta(seconds=timeout_seconds)
    timeout_task = context.create_timer(deadline)

    approval_task = context.wait_for_external_event("ApprovedSources")

    winner = yield context.task_any([approval_task, timeout_task])

    if winner == approval_task:
        timeout_task.cancel()
        approved_urls = approval_task.result
        context.set_custom_status(
            {
                "message": f"Approved {len(approved_urls)} sources. scraping and summarizing..."
            }
        )
        parallel_tasks = []
        for url in approved_urls:
            parallel_tasks.append(context.call_activity("scrape_activity", url))

        scraped_texts = yield context.task_all(parallel_tasks)
        final_report = yield context.call_activity(
            "summarize_activity", {"topic": topic, "scraped_texts": scraped_texts}
        )
        return final_report
    else:
        context.set_custom_status(
            {"message": "Approval timed out. `Orchestration cancelled"}
        )
        return "Approval timed out"
