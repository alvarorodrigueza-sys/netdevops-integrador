\# DEVICE ONBOARDING



Before configuring a new laboratory device:



1\. Determine what device/platform it is.

2\. Determine how the laptop can communicate with it:

&#x20;  - SSH generic -> paramiko

&#x20;  - Cisco/FortiGate/network CLI -> netmiko

&#x20;  - Serial console -> serial

&#x20;  - MQTT -> mqtt

&#x20;  - HTTP/REST -> http

&#x20;  - SNMP -> snmp

&#x20;  - NETCONF -> netconf



3\. Add or update the device in inventory.yaml.



4\. Put credentials only in:

&#x20;  %USERPROFILE%\\.lab-agent\\secrets.env



5\. Enable only devices actually used by the current laboratory.



6\. Test connectivity before configuration.



7\. Perform read-only inspection before making changes.



8\. After configuration, verify operational state using appropriate commands.



9\. If something fails:

&#x20;  - inspect the full result

&#x20;  - identify the failure category

&#x20;  - gather logs/status/output

&#x20;  - apply the smallest correction

&#x20;  - retest



10\. Never assume a configuration succeeded.

