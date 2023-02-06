This Python script is used to compare two releases of an Octopus project and display details such as:

* Packages that were added or removed
* Files that were changed in the packages
* Variables that were changed
* Steps that were changed

Usage:

```bash
python3 -m venv my_env
. my_env/bin/activate
pip --disable-pip-version-check install -r requirements.txt
python main.py \
    --octopusUrl https://yourinstance.octopus.app \
    --octopusApiKey API-APIKEYGOESHERE \
    --octopusSpace Default \
    --octopusProject "The Project Name"
```