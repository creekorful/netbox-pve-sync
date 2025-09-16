# netbox-pve-sync

<<<<<<< HEAD
Synchronize Proxmox Virtual Environment (PVE) information to a NetBox instance.
This allows automatic tracking of Virtual Machines, disks, IP addresses/prefixes, MAC addresses, VLANs, ...

## How does it work?

This script work by pulling VMs information from the PVE API and create/update/delete resources on NetBox.

## Installation

This package is available on PyPi. You can install it using pip.

```
$ pip install netbox-pve-sync
```

## Configuration

### On NetBox

You'll need to create a dedicated user (ex: netbox-pve-sync) on your NetBox instance and then create a write API
token.

The following env variables will need to be set:

- **NB_API_URL**: The URL to your NetBox instance. (ex: https://netbox.example.org)
- **NB_API_TOKEN**: The token created previously. (ex: f74cb99cf552b7005fd1a616b53efba2ce0c9656)

You can also set the `NB_CLUSTER_ID` env variable in order to indicate the ID of the cluster that will be used in
NetBox.

You'll also need to perform a minimal configuration on NetBox:

- Create the physical nodes hosting the cluster. (The name should match the one on Proxmox, so that the script can
  correctly link the VMs to the physical host)
- Create the cluster.
- Add the following Custom Fields:

| Name       | Object types    | Label      | Type    |
|------------|-----------------|------------|---------|
| autostart  | Virtual Machine | Autostart  | Boolean |
| replicated | Virtual Machine | Replicated | Boolean |
| ha         | Virtual Machine | Failover   | Boolean |
| backup     | Virtual Disk    | Backup     | Boolean |
| dns_name   | Prefix          | DNS Name   | Text    |

### On the PVE API

You'll need to create a dedicated user (ex: netsync) on your PVE cluster and then create an API token.

The user needs to have access to the VM.Monitor, Pool.Audit, VM.Audit, Sys.Audit permissions.

The following env variables will need to be set:

- **PVE_API_HOST**: The DNS/IP to your PVE instance. (ex: 10.10.0.10)
- **PVE_API_USER**: The username of the account created previously. (ex: netsync@pve)
- **PVE_API_TOKEN**: The name of the API token created previously. (ex: test-token)
- **PVE_API_SECRET**: The API token created previously (ex: 4d46dc0a-6363-47a2-98df-d5cdfefa33d2)

## Executing the script

You can then execute the script using the following command:

```
PVE_API_HOST=xx PVE_API_USER=xx PVE_API_TOKEN=xx PVE_API_SECRET=xx NB_API_URL=xx NB_API_TOKEN=xx nbpxsync
```
