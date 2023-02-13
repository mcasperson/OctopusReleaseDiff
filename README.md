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

When used as an Octopus step, you would call it like this:

```bash
python3 -m venv my_env
. my_env/bin/activate
pip --disable-pip-version-check install -r requirements.txt
python main.py \
  --octopusUrl https://yourinstance.octopus.app \
  --octopusApiKey API-APIKEYGOESHERE \
  --octopusSpace "#{Octopus.Space.Name}" \
  --octopusProject "#{Octopus.Project.Name}" \
  --oldRelease "#{Octopus.Release.PreviousForEnvironment.Number}" \
  --newRelease "#{Octopus.Release.Number}"
```

The script has also been packaged as a Docker image for convenience, and can be run in a script step like this:

```bash
echo "##octopus[stdout-verbose]"
docker pull mcasperson/octopusreleasediff
echo "##octopus[stdout-default]"

docker run mcasperson/octopusreleasediff \
  --octopusUrl https://yourinstance.octopus.app \
  --octopusApiKey API-APIKEYGOESHERE \
  --octopusSpace "#{Octopus.Space.Name}" \
  --octopusProject "#{Octopus.Project.Name}" \
  --oldRelease "#{Octopus.Release.PreviousForEnvironment.Number}" \
  --newRelease "#{Octopus.Release.Number}"
```

## Output Variables

This script generates many [Octopus output variables](https://octopus.com/docs/projects/variables/output-variables)
that allows the results of the release diff to be consumed in subsequent steps.

* `Files[<package id>].Added` is a comma separated list of files added in the package referenced by the new release.
* `Files[<package id>].Removed` is a comma separated list of files removed in the package referenced by the new release.
* `Files[<package id>].Changed` is a comma separated list of files changed in the package referenced by the new release.
* `FileDiff[<package id>].Files[<file>].Diff` is the diff of a file that changed between the two releases.
* `Variables.Added` is a comma separated list of variables added in this release.
* `Variables.Removed` is a comma separated list of variables removed in this release.
* `Variables.Changed` is a comma separated list of variables changed in this release.
* `Variables.ScopeChanged` is a comma separated list of variables changed in this release.
* `Variables[<variable name and index>].Changed` is a JSON representation of the named variable whose value has changed.
* `Variables[<variable name and index>].ScopeChanged` is a JSON representation of the named variable whose scope has changed.
* `Packages.Added` is a comma separated list of packages that where added by the new release.
* `Packages.Removed` is a comma separated list of packages that where removed by the new release.
* `Steps.Diff` contains the diff of the steps between the two releases.

These variables can be used with the [Octostache repetition syntax](https://octopus.com/docs/projects/variables/variable-substitutions#VariableSubstitutionSyntax-Repetition)
to extract useful information in subsequent steps:

```bash
#{each package in Octopus.Action[Calculate Release Diff].Output.Files}
 #{if package.Added}echo "Package #{package} added files #{package.Added}"#{/if}
 #{if package.Removed}echo "Package #{package} removed files #{package.Removed}"#{/if}
 #{if package.Changed}echo "Package #{package} changed files #{package.Changed}"#{/if}
#{/each}

#{each package in Octopus.Action[Calculate Release Diff].Output.FileDiff}
  #{each file in package.Files}
echo "Changes to file #{file}"
cat << EOF
#{file.Diff}
EOF
  #{/each}
#{/each}
```
