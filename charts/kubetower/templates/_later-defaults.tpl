{{/*
The defaults of every value added after this chart's first version.

`helm upgrade --reuse-values` renders the new templates with the old
release's values and does not merge the new chart's defaults into them, so a
value added since is absent - and a template reading `.Values.auth.oidc.issuer`
under an absent `auth.oidc` stops with a nil pointer, which is an upgrade that
cannot be done. Absent has to mean the default, not a different behaviour.

So every template starts with kubetower.fillLaterDefaults, which adds what is
missing from the values and touches nothing that is there - a `false` stays
false, which `default` and sprig's `merge` would both turn back into the
default. Every template, because which one Helm renders first is Helm's
business.

The values below repeat values.yaml, and test/reuse-values-check.py refuses
the day they differ from it, or the day values.yaml gains a key the first
version did not have and this list does not carry.
*/}}
{{- define "kubetower.laterDefaults" -}}
auth:
  localAccount: false
  oidc:
    issuer: ""
    discoveryURL: ""
    clientID: ""
    clientSecret:
      existingSecret: ""
      key: client-secret
    redirectURL: ""
    scopes: openid,email,profile
    usernameClaim: ""
    groupsClaim: ""
    groupsPrefix: ""
    sessionMax: ""
{{- end }}

{{- define "kubetower.fillLaterDefaults" -}}
{{- include "kubetower.fillMissing" (list .Values (include "kubetower.laterDefaults" . | fromYaml)) -}}
{{- end }}

{{/* Add to the map (index . 0) every key of (index . 1) it lacks, recursively. */}}
{{- define "kubetower.fillMissing" -}}
{{- $dst := index . 0 -}}
{{- range $key, $value := index . 1 -}}
{{- if not (hasKey $dst $key) -}}
{{- $_ := set $dst $key $value -}}
{{- else if and (kindIs "map" $value) (kindIs "map" (index $dst $key)) -}}
{{- include "kubetower.fillMissing" (list (index $dst $key) $value) -}}
{{- end -}}
{{- end -}}
{{- end }}
