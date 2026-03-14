---
trigger: always_on
---

strictly follow the file structure convention given below: 

semabridge/
——src/
————semabridge/
——————core/
——————connectors/
——————formats/
——————converter/
——————intermediate/ (set this as osi/osi depending on user chosen settings) 
——————repository/
——————cli/
——————plugins/
——————utils/
——tests/
——docs/
——examples/
——scripts/

ensure that any modifications made or testing does not result in violation of this file strcuture. do not place files outside these directories unnecessarily. 