---
title: "A failed zip deploy of an Azure Function App can leave a clean plan and old code"
date: 2026-10-17
---

# A failed zip deploy of an Azure Function App can leave a clean plan and old code

Provider version: `hashicorp/azurerm` 4.81.0, which uses
`terraform-plugin-sdk/v2` 2.40.1 ([go.mod](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/go.mod)).

## What the docs say

The azurerm doc for `zip_deploy_file` says nothing about a failed deploy.
The SDK documents the state rule in a code comment on `ResourceData.Partial`:
"during an update when an error is returned, the proposed config is set into
state, even without any calls to d.Set."
([resource_data.go, v2.40.1](https://github.com/hashicorp/terraform-plugin-sdk/blob/v2.40.1/helper/schema/resource_data.go))
For the remote build, Microsoft says that it "installs all dependencies listed
in `requirements.txt` into the correct directory", and names that directory
`./.python_packages/lib/site-packages`
([Python build options](https://learn.microsoft.com/en-us/azure/azure-functions/python-build-options)).

## What we read in the source

These are facts from the source code at the pinned versions. We did not run a
failing deploy.

1. The Update function of `azurerm_linux_function_app` runs the zip deploy
   last, and only `if metadata.ResourceData.HasChange("zip_deploy_file")`.
   It returns the error of the deploy as is.
   ([linux_function_app_resource.go, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/appservice/linux_function_app_resource.go))
2. The deploy is a `POST` to `<scm host>/api/zipdeploy?isAsync=true`. A status
   other than 200 or 202, or a failed deployment status, is an error.
   ([publish_app.go, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/appservice/helpers/publish_app.go))
3. The azurerm wrapper returns that error with no call to `d.Partial(true)`.
   A search of both files for `Partial` finds nothing.
   ([wrapper_resource.go, v4.81.0](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/sdk/wrapper_resource.go))
4. Read cannot get the zip from Azure. Its code comment says: "Zip Deploys are
   not retrievable, so attempt to get from config." It copies the value from
   state. A refresh does not reset the value.

## What we infer

From 1 to 4: after a failed zip deploy, state holds the new `zip_deploy_file`
path, the app is not tainted, and the next plan shows no change. The "not
tainted" part is our reading, not a sourced fact: this repo cites no primary
source that an Update error does not taint a resource. The
old code keeps running. This is an inference from reading the code. We have not
seen it happen in a live apply.

The remote build adds `.python_packages` to the deployed folder (documented
above). The sweeper hash covers only the source files, so this folder cannot
change the hash. Other files that the remote build adds are not documented.

## Why it matters

A clean plan after an apply does not prove that the new code runs. The check
`make verify/azure-sweeper` compares the `code_sha` that the sweeper logs with
the `sweeper_code_sha` output. It fails closed: it also fails when the sweeper
logs nothing. It is correct whether or not the inference is true.

## Sources

- [terraform-plugin-sdk v2.40.1, `ResourceData.Partial`](https://github.com/hashicorp/terraform-plugin-sdk/blob/v2.40.1/helper/schema/resource_data.go)
- [terraform-provider-azurerm v4.81.0, linux_function_app_resource.go](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/appservice/linux_function_app_resource.go)
- [terraform-provider-azurerm v4.81.0, publish_app.go](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/appservice/helpers/publish_app.go)
- [terraform-provider-azurerm v4.81.0, wrapper_resource.go](https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/sdk/wrapper_resource.go)
- [Python build options (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/azure-functions/python-build-options)
- [Zip deployment for Azure Functions (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/azure-functions/deployment-zip-push)
