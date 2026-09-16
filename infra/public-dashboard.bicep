param location string
param appName string = 'skillops-dashboard'

@allowed(['Standard'])
param skuName string = 'Standard'

resource dashboard 'Microsoft.Web/staticSites@2025-03-01' = {
  name: appName
  location: location
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    allowConfigFileUpdates: true
    publicNetworkAccess: 'Enabled'
    buildProperties: {
      skipGithubActionWorkflowGeneration: true
    }
  }
  tags: {
    project: 'skillops'
    purpose: 'public-evaluation-dashboard'
  }
}

output appName string = dashboard.name
output dashboardUrl string = 'https://${dashboard.properties.defaultHostname}'
