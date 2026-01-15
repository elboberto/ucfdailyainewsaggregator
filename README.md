# UCF Daily AI News Aggregator

An Azure Functions application that aggregates AI news daily for UCF (University of Central Florida).

## Project Structure

- `function_app.py` - Main Azure Functions application code
- `requirements.txt` - Python dependencies
- `config.json` - Configuration settings
- `host.json` - Azure Functions host configuration

## Getting Started

### Prerequisites

- Python 3.9+
- Azure Functions Core Tools
- Azure CLI (optional, for deployment)

### Installation

1. Clone the repository:
```bash
git clone <your-repo-url>
cd ucfdailyainewsaggregator
```

2. Create a virtual environment:
```bash
python -m venv .venv
.venv\Scripts\activate  # On Windows
source .venv/bin/activate  # On macOS/Linux
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

### Running Locally

Start the Azure Functions runtime:
```bash
func start
```

## Configuration

Configure the application using `config.json` for application settings.

## Scheduling

The function runs daily at **6 AM ET (11:00 UTC)** via GitHub Actions.

### Setup GitHub Actions Scheduling

1. Get your Azure Function URL:
   - In Azure Portal → Your Function App → Functions → AINewsDigest → Get Function URL
   - Copy the URL

2. Add the secret to GitHub:
   - Go to your repository → Settings → Secrets and variables → Actions
   - Click "New repository secret"
   - Name: `FUNCTION_URL`
   - Value: Paste your function URL (including the function key)

3. The workflow will automatically trigger daily or use the "Run workflow" button to test manually.

## Deployment

Deploy to Azure using Azure CLI:
```bash
func azure functionapp publish <FunctionAppName>
```

Or use the Azure Functions VS Code extension for easy deployment.

## License

MIT
