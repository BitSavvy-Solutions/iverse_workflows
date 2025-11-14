<#
.SYNOPSIS
    Deploys Azure Function App to Azure.
.DESCRIPTION
    This script handles the deployment of an Azure Function App to Azure,
    including prerequisite checks and error handling.
.NOTES
    File Name      : deploy-func.ps1
    Author         : Desiree Capacia
    Prerequisite   : Azure CLI, Azure Functions Core Tools
    Version        : 1.0
#>

# Import configuration
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $scriptPath "config.json"
$config = Get-Content -Path $configPath | ConvertFrom-Json

# Configuration Variables
$functionAppName = $config.functionAppName
$resourceGroupName = $config.resourceGroupName
$subscriptionId = $config.subscriptionId
$location = $config.location
$runtime = $config.runtime
$operatingSystem = $config.operatingSystem
$planName = $config.planName
$planSku = $config.planSku

# Function to check if Azure CLI is installed
function Test-AzureCLI {
    try {
        az --version | Out-Null
        return $true
    }
    catch {
        Write-Error "Azure CLI is not installed. Please install it first."
        return $false
    }
}

# Function to check if Azure Functions Core Tools is installed
function Test-FuncTools {
    try {
        func --version | Out-Null
        return $true
    }
    catch {
        Write-Error "Azure Functions Core Tools is not installed. Please install it first."
        return $false
    }
}

# Main deployment script
function Deploy-AzureFunction {
    Write-Host "Starting Azure Function deployment process..." -ForegroundColor Green

    # Check prerequisites
    if (-not (Test-AzureCLI) -or -not (Test-FuncTools)) {
        Write-Host "Prerequisites not met. Exiting..." -ForegroundColor Red
        return
    }

    try {
        # Ensure we're in the correct subscription
        Write-Host "Setting Azure subscription context..." -ForegroundColor Yellow
        az account set --subscription $subscriptionId

        # Verify Function App exists
        Write-Host "Verifying Function App exists..." -ForegroundColor Yellow
        $functionApp = az functionapp show --name $functionAppName --resource-group $resourceGroupName
        if (-not $functionApp) {
            throw "Function App '$functionAppName' not found in resource group '$resourceGroupName'"
        }

        # Verify the function app configuration matches expectations
        Write-Host "Verifying Function App configuration..." -ForegroundColor Yellow
        $functionAppConfig = az functionapp config show --name $functionAppName --resource-group $resourceGroupName
        if ($functionAppConfig) {
            # Ensure Linux OS
            Write-Host "Verifying operating system configuration..." -ForegroundColor Yellow
            if ($operatingSystem -eq "Linux") {
                az functionapp config set --linux-fx-version "PYTHON|$runtime" --name $functionAppName --resource-group $resourceGroupName
            }
        }

        # Navigate to function app source directory
        $srcPath = Join-Path $scriptPath "../../src/etl"
        Push-Location $srcPath

        # Deploy the function
        Write-Host "Publishing Function App..." -ForegroundColor Yellow
        func azure functionapp publish $functionAppName

        # Return to original directory
        Pop-Location

        Write-Host "Deployment completed successfully!" -ForegroundColor Green
        Write-Host "Function App URL: https://$functionAppName.azurewebsites.net" -ForegroundColor Cyan
    }
    catch {
        Write-Error "An error occurred during deployment: $_"
        Write-Host "Deployment failed!" -ForegroundColor Red
        
        # Ensure we return to original directory even if there's an error
        if ((Get-Location).Path -eq $srcPath) {
            Pop-Location
        }
    }
}

# Execute the deployment
Deploy-AzureFunction