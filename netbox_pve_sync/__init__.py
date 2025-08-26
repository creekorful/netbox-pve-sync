# pylint: disable=fixme,too-many-branches

"""
netbox-pve-sync: Synchronize Proxmox Virtual Environment (PVE) information to a NetBox instance
"""

import os
import sys
from typing import Optional

import pynetbox
import urllib3
from proxmoxer import ProxmoxAPI, ResourceException


def _load_nb_objects(_nb_api: pynetbox.api) -> dict:
    _nb_objects = {
        'devices': {},
        'virtual_machines': {},
        'virtual_machines_interfaces': {},
        'mac_addresses': {},
        'prefixes': {},
        'ip_addresses': {},
        'vlans': {},
        'disks': {},
        'tags': {},
    }

    # Load NetBox devices
    for _nb_device in _nb_api.dcim.devices.all():
        _nb_objects['devices'][_nb_device.name.lower()] = _nb_device

    # Load NetBox virtual machines
    for _nb_virtual_machine in _nb_api.virtualization.virtual_machines.all():
        _nb_objects['virtual_machines'][_nb_virtual_machine.serial] = _nb_virtual_machine

    # Load NetBox interfaces
    for _nb_interface in _nb_api.virtualization.interfaces.all():
        if _nb_interface.virtual_machine.id not in _nb_objects['virtual_machines_interfaces']:
            _nb_objects['virtual_machines_interfaces'][_nb_interface.virtual_machine.id] = {}

        _nb_objects['virtual_machines_interfaces'][_nb_interface.virtual_machine.id][_nb_interface.name] = _nb_interface

    # Load NetBox mac addresses
    for _nb_mac_address in _nb_api.dcim.mac_addresses.all():
        _nb_objects['mac_addresses'][_nb_mac_address.mac_address] = _nb_mac_address

    # Load NetBox IP ranges
    for _nb_prefix in _nb_api.ipam.prefixes.all():
        _nb_objects['prefixes'][_nb_prefix.prefix] = _nb_prefix

    # Load NetBox IP addresses
    for _nb_ip_address in _nb_api.ipam.ip_addresses.all():
        _nb_objects['ip_addresses'][_nb_ip_address['address']] = _nb_ip_address

    # Load NetBox vLANs
    for _nb_vlan in _nb_api.ipam.vlans.all():
        _nb_objects['vlans'][str(_nb_vlan.vid)] = _nb_vlan

    # Load NetBox disks
    for _nb_disk in _nb_api.virtualization.virtual_disks.all():
        if _nb_disk.virtual_machine.id not in _nb_objects['disks']:
            _nb_objects['disks'][_nb_disk.virtual_machine.id] = {}

        _nb_objects['disks'][_nb_disk.virtual_machine.id][_nb_disk.name] = _nb_disk

    # Load NetBox tags
    for _nb_tag in _nb_api.extras.tags.all():
        _nb_objects['tags'][_nb_tag.name] = _nb_tag

    return _nb_objects


def _process_pve_tags(
        _pve_api: ProxmoxAPI,
        _nb_api: pynetbox.api,
        _nb_objects: dict,
) -> dict:
    # TODO: First tags

    # Then pool (we treat them as tags)
    for _pve_pool in _pve_api.pools.get():
        _tag_name = f'Pool/{_pve_pool["poolid"]}'
        _nb_tag = _nb_objects['tags'].get(_tag_name)
        if _nb_tag is None:
            _nb_tag = _nb_api.extras.tags.create(
                name=_tag_name,
                slug=f'pool-{_pve_pool["poolid"]}'.lower(),
                description=f'Proxmox pool {_pve_pool["poolid"]}',
            )
            _nb_objects['tags'][_nb_tag.name] = _nb_tag

    return _nb_objects


def _process_pve_virtual_machine(
        _pve_api: ProxmoxAPI,
        _nb_api: pynetbox.api,
        _nb_objects: dict,
        _nb_device: any,
        _pve_tags: [str],
        _pve_virtual_machine: dict,
        _is_replicated: bool,
        _has_ha: bool,
) -> dict:
    _pve_node_name = _nb_device.name.lower()

    pve_virtual_machine_config = _pve_api.nodes(_pve_node_name).qemu(_pve_virtual_machine['vmid']).config.get()

    try:
        pve_virtual_machine_agent_interfaces = _pve_api \
            .nodes(_pve_node_name) \
            .qemu(_pve_virtual_machine['vmid']) \
            .agent('network-get-interfaces') \
            .get()
    except ResourceException:
        pve_virtual_machine_agent_interfaces = {'result': []}

    # Extract IP addresses from QEMU
    pve_virtual_machine_ip_addresses = {}
    for result in pve_virtual_machine_agent_interfaces['result']:
        pve_virtual_machine_ip_addresses[result['name']] = result['ip-addresses']

    # Create the virtual machine if it exists, update it otherwise
    nb_virtual_machine = _nb_objects['virtual_machines'].get(str(_pve_virtual_machine['vmid']))
    if nb_virtual_machine is None:
        nb_virtual_machine = _nb_api.virtualization.virtual_machines.create(
            serial=_pve_virtual_machine['vmid'],
            name=_pve_virtual_machine['name'],
            site=_nb_device.site.id,
            cluster=os.environ.get('NB_CLUSTER_ID', 1),
            device=_nb_device.id,
            vcpus=pve_virtual_machine_config['cores'],
            memory=int(pve_virtual_machine_config['memory']),
            status='active' if _pve_virtual_machine['status'] == 'running' else 'offline',
            tags=list(map(lambda _pve_tag_name: _nb_objects['tags'][_pve_tag_name].id, _pve_tags)),
            custom_fields={
                'autostart': pve_virtual_machine_config.get('onboot') == 1,
                'replicated': _is_replicated,
                'ha': _has_ha,
            }
        )
    else:
        nb_virtual_machine.name = _pve_virtual_machine['name']
        nb_virtual_machine.site = _nb_device.site.id
        nb_virtual_machine.cluster = os.environ.get('NB_CLUSTER_ID', 1)
        nb_virtual_machine.device = _nb_device.id
        nb_virtual_machine.vcpus = pve_virtual_machine_config['cores']
        nb_virtual_machine.memory = int(pve_virtual_machine_config['memory'])
        nb_virtual_machine.status = 'active' if _pve_virtual_machine['status'] == 'running' else 'offline'
        nb_virtual_machine.tags = list(map(lambda _pve_tag_name: _nb_objects['tags'][_pve_tag_name].id, _pve_tags))
        nb_virtual_machine.custom_fields['autostart'] = pve_virtual_machine_config.get('onboot') == 1
        nb_virtual_machine.custom_fields['replicated'] = _is_replicated
        nb_virtual_machine.custom_fields['ha'] = _has_ha
        nb_virtual_machine.save()

    # Handle the VM network interfaces
    _process_pve_virtual_machine_network_interfaces(
        _nb_api,
        _nb_objects,
        pve_virtual_machine_config,
        nb_virtual_machine,
        pve_virtual_machine_ip_addresses,
    )

    # Handle the VM disks
    _process_pve_virtual_machine_disks(
        _nb_api,
        _nb_objects,
        pve_virtual_machine_config,
        nb_virtual_machine,
    )

    return _nb_objects


def _process_pve_virtual_machine_network_interfaces(
        _nb_api: pynetbox.api,
        _nb_objects: dict,
        _pve_virtual_machine_config: dict,
        _nb_virtual_machine: any,
        _pve_virtual_machine_ip_addresses: dict,
) -> dict:
    # Handle the VM network interfaces
    for (_config_key, _config_value) in _pve_virtual_machine_config.items():
        if not _config_key.startswith('net'):
            continue

        _network_definition = _parse_pve_network_definition(_config_value)

        # Determinate MAC address
        network_mac_address = None
        for _model in ['virtio', 'e1000']:
            if _model in _network_definition:
                network_mac_address = _network_definition[_model]
                break

        if network_mac_address is None:
            continue

        _process_pve_virtual_machine_network_interface(
            _nb_api,
            _nb_objects,
            _nb_virtual_machine,
            _config_key,
            network_mac_address,
            _network_definition.get('tag'),
            _pve_virtual_machine_ip_addresses,
        )

    return _nb_objects


def _process_pve_virtual_machine_network_interface(
        _nb_api: pynetbox.api,
        _nb_objects: dict,
        _nb_virtual_machine: any,
        _interface_name: str,
        _interface_mac_address: str,
        _interface_vlan_id: Optional[int],
        _pve_virtual_machine_ip_addresses: dict,
) -> dict:
    nb_virtual_machines_interface = _nb_objects['virtual_machines_interfaces'] \
        .get(_nb_virtual_machine.id, {}) \
        .get(_interface_name)

    if nb_virtual_machines_interface is None:
        nb_virtual_machines_interface = _nb_api.virtualization.interfaces.create(
            virtual_machine=_nb_virtual_machine.id,
            name=_interface_name,
            description=_interface_mac_address,
        )

        if _nb_virtual_machine.id not in _nb_objects['virtual_machines_interfaces']:
            _nb_objects['virtual_machines_interfaces'][_nb_virtual_machine.id] = {}

        _nb_objects['virtual_machines_interfaces'][_nb_virtual_machine.id][
            _interface_name] = nb_virtual_machines_interface

    # Create the MAC address and link it to the VM
    nb_mac_address = _nb_objects['mac_addresses'].get(_interface_mac_address)
    if nb_mac_address is None:
        nb_mac_address = _nb_api.dcim.mac_addresses.create(
            mac_address=_interface_mac_address,
            assigned_object_type='virtualization.vminterface',
            assigned_object_id=nb_virtual_machines_interface.id,
        )

        _nb_objects['mac_addresses'][_interface_mac_address] = nb_mac_address

        nb_virtual_machines_interface.primary_mac_address = nb_mac_address.id
        nb_virtual_machines_interface.save()

    # TODO: Improve Multiple IP address handling
    _pve_virtual_machine_ip_address = None
    for raw_interface_name in ['eth0', 'ens18', 'ens19']:
        if raw_interface_name in _pve_virtual_machine_ip_addresses:
            _pve_virtual_machine_ip_address = _pve_virtual_machine_ip_addresses[raw_interface_name][0]
            break

    if _pve_virtual_machine_ip_address is not None:
        _virtual_machine_address = _pve_virtual_machine_ip_address['ip-address']
        _virtual_machine_address_mask = _pve_virtual_machine_ip_address['prefix']
        _virtual_machine_full_address = f'{_virtual_machine_address}/{_virtual_machine_address_mask}'

        # First, determinate if the prefix exists
        _prefix_network_address = '.'.join(_virtual_machine_address.split('.')[:-1]) + '.0'
        _prefix_network_full_address = f'{_prefix_network_address}/{_virtual_machine_address_mask}'

        nb_prefix = _nb_objects['prefixes'].get(_prefix_network_full_address)
        if nb_prefix is None:
            nb_prefix = _nb_api.ipam.prefixes.create(prefix=_prefix_network_full_address)
            _nb_objects['prefixes'][nb_prefix.prefix] = nb_prefix

        if 'dns_name' in nb_prefix.custom_fields and nb_prefix.custom_fields['dns_name'] is not None:
            ip_address_dns_name = f'{_nb_virtual_machine.name}.{nb_prefix.custom_fields["dns_name"]}'
        else:
            ip_address_dns_name = ''

        nb_ip_address = _nb_objects['ip_addresses'].get(_virtual_machine_full_address)
        if nb_ip_address is None:
            nb_ip_address = _nb_api.ipam.ip_addresses.create(
                address=_virtual_machine_full_address,
                assigned_object_type='virtualization.vminterface',
                assigned_object_id=nb_virtual_machines_interface.id,
                dns_name=ip_address_dns_name
            )
            _nb_objects['ip_addresses'][nb_ip_address.address] = nb_ip_address
        else:
            nb_ip_address.assigned_object_type = 'virtualization.vminterface'
            nb_ip_address.assigned_object_id = nb_virtual_machines_interface.id
            nb_ip_address.dns_name = ip_address_dns_name
            nb_ip_address.save()

        _nb_virtual_machine.primary_ip4 = nb_ip_address.id
        _nb_virtual_machine.save()

        # Handle VLAN
        if _interface_vlan_id is not None:
            nb_vlan = _nb_objects['vlans'].get(str(_interface_vlan_id))
            if nb_vlan is None:
                nb_vlan = _nb_api.ipam.vlans.create(
                    vid=_interface_vlan_id,
                    name=f'VLAN {_interface_vlan_id}',
                )
                _nb_objects['vlans'][_interface_vlan_id] = nb_vlan

            nb_prefix.vlan = nb_vlan.id
            nb_prefix.save()

    return _nb_objects


def _process_pve_virtual_machine_disks(
        _nb_api: pynetbox.api,
        _nb_objects: dict,
        _pve_virtual_machine_config: dict,
        _nb_virtual_machine: any,
) -> dict:
    # Handle the VM disks
    for (_config_key, _config_value) in _pve_virtual_machine_config.items():
        if not _config_key.startswith('scsi'):
            continue
        if _config_key == 'scsihw':
            continue

        _disk_definition = _parse_pve_disk_definition(_config_value)

        _process_pve_virtual_machine_disk(
            _nb_api,
            _nb_objects,
            _nb_virtual_machine,
            _disk_definition['name'],
            _process_pve_disk_size(_disk_definition['size']),
            _disk_definition.get('backup', '1') == '1',
        )

    return _nb_objects


def _process_pve_virtual_machine_disk(
        _nb_api: pynetbox.api,
        _nb_objects: dict,
        _nb_virtual_machine: any,
        _disk_name: str,
        _disk_size: int,
        _has_backup: bool,
) -> dict:
    nb_disk = _nb_objects['disks'].get(_nb_virtual_machine.id, {}).get(_disk_name)
    if nb_disk is None:
        _nb_api.virtualization.virtual_disks.create(
            name=_disk_name,
            size=_disk_size,
            virtual_machine=_nb_virtual_machine.id,
            custom_fields={
                'backup': _has_backup,
            }
        )
    else:
        nb_disk.size = _disk_size
        nb_disk.custom_fields['backup'] = _has_backup
        nb_disk.save()

    return _nb_objects


def _parse_pve_network_definition(_raw_network_definition: str) -> dict:
    _network_definition = {}

    for _component in _raw_network_definition.split(','):
        _component_parts = _component.split('=')
        _network_definition[_component_parts[0]] = _component_parts[1]

    return _network_definition


def _parse_pve_disk_definition(_raw_disk_definition: str) -> dict:
    _disk_definition = {}

    for _component in _raw_disk_definition.split(','):
        _component_parts = _component.split('=')
        if len(_component_parts) == 1:
            _disk_definition['name'] = _component_parts[0]
        else:
            _disk_definition[_component_parts[0]] = _component_parts[1]

    return _disk_definition


def _process_pve_disk_size(_raw_disk_size: str) -> int:
    size = _raw_disk_size[:-1]
    size_unit = _raw_disk_size[-1]

    if size_unit == 'M':
        return int(size)
    if size_unit == 'G':
        return int(size) * 1_000
    if size_unit == 'T':
        return int(size) * 1_000_000

    return -1


def main():
    """
    netbox-pve-sync main entrypoint
    """

    # Instantiate connection to the Proxmox VE API
    pve_api = ProxmoxAPI(
        host=os.environ['PVE_API_HOST'],
        user=os.environ['PVE_API_USER'],
        token_name=os.environ['PVE_API_TOKEN'],
        token_value=os.environ['PVE_API_SECRET'],
        verify_ssl=os.getenv('PVE_API_VERIFY_SSL', 'false').lower() == 'true',
    )

    # Instantiate connection to the Netbox API
    nb_api = pynetbox.api(
        url=os.environ['NB_API_URL'],
        token=os.environ['NB_API_TOKEN'],
    )

    # Load NetBox objects
    nb_objects = _load_nb_objects(nb_api)

    # Process Proxmox tags
    _process_pve_tags(
        pve_api,
        nb_api,
        nb_objects,
    )

    # Fetch VM tags from Proxmox
    pve_vm_tags = {}
    for pve_vm_resource in pve_api.cluster.resources.get(type='vm'):
        pve_vm_tags[pve_vm_resource['vmid']] = []

        if 'pool' in pve_vm_resource:
            pve_vm_tags[pve_vm_resource['vmid']].append(f'Pool/{pve_vm_resource["pool"]}')

        if 'tags' in pve_vm_resource:
            pass  # TODO: pve_vm_tags[pve_vm_resource['vmid']].append(pve_vm_resource['tags'])

    pve_ha_virtual_machine_ids = list(
        map(
            lambda r: int(r['sid'].split(':')[1]),
            filter(lambda r: r['type'] == 'service', pve_api.cluster.ha.status.current.get())
        )
    )

    # Process Proxmox nodes
    for pve_node in pve_api.nodes.get():
        pve_replicated_virtual_machine_ids = list(
            map(lambda r: r['guest'], pve_api.nodes(pve_node['node']).replication.get())
        )

        # This script does not create the hardware devices.
        nb_device = nb_objects['devices'].get(pve_node['node'].lower())
        if nb_device is None:
            print(f'The device {pve_node["node"]} is not created on NetBox. Exiting.')
            sys.exit(1)
        else:
            nb_device.status = 'active' if pve_node['status'] == 'online' else 'offline'
            nb_device.save()

        # Process Proxmox virtual machines per node
        for pve_virtual_machine in pve_api.nodes(pve_node['node']).qemu.get():
            _process_pve_virtual_machine(
                pve_api,
                nb_api,
                nb_objects,
                nb_device,
                pve_vm_tags.get(pve_virtual_machine['vmid'], []),
                pve_virtual_machine,
                pve_virtual_machine['vmid'] in pve_replicated_virtual_machine_ids,
                pve_virtual_machine['vmid'] in pve_ha_virtual_machine_ids,
            )


if __name__ == '__main__':
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    main()
