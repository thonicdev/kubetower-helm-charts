{{/*
Several replicas, and the Valkey they share.

Everything about `replicaCount` above one lives in this file, valkey.yaml,
valkey-secret.yaml, valkey-config.yaml and replicas-refused.yaml, so that the
console's own templates carry one include each rather than the whole
mechanism.
*/}}

{{/* Whether this release runs more than one console. */}}
{{- define "kubetower.several" -}}
{{- if gt (int .Values.replicaCount) 1 }}true{{ end }}
{{- end }}

{{- define "kubetower.valkeyName" -}}
{{- printf "%s-valkey" (include "kubetower.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kubetower.valkeySelectorLabels" -}}
app.kubernetes.io/name: {{ include "kubetower.name" . }}-valkey
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* The Secret holding the Valkey password: the operator's, or this chart's. */}}
{{- define "kubetower.valkeySecretName" -}}
{{- if .Values.valkey.existingSecret }}
{{- .Values.valkey.existingSecret }}
{{- else }}
{{- include "kubetower.valkeyName" . }}
{{- end }}
{{- end }}

{{- define "kubetower.valkeySecretKey" -}}
{{- if .Values.valkey.existingSecret }}
{{- .Values.valkey.existingSecretKey }}
{{- else -}}
password
{{- end }}
{{- end }}

{{/*
The password, drawn once and kept.

What is already in the cluster, else a new one: the Secret the last install
wrote is read back first, so a `helm upgrade` keeps the password.

**The limit of `lookup`, stated rather than discovered**: it answers only when
Helm talks to a cluster. `helm template`, `helm install --dry-run` and every
GitOps tool that renders without one - Argo CD among them - get nothing back,
so each of their renders draws a new password, and applying it changes the
Secret. The checksums below then roll Valkey and every console together, so
each sync signs everybody out; without them, pods started after the change
would hold the new password while the running ones held the old, and requests
would fail on whichever pod was out of step. Under those tools, set
`valkey.existingSecret` to a Secret you manage - and auth.sessionSecret or
auth.existingSecret too, for the same reason.

Drawn once per render and remembered for the rest of it, so the Secret and the
checksums over it describe the same password.
*/}}
{{- define "kubetower.valkeyPassword" -}}
{{- if not (hasKey .Values.valkey "_drawnPassword") }}
{{- $value := "" }}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "kubetower.valkeyName" .) }}
{{- if and $existing $existing.data (index $existing.data "password") }}
{{- $value = index $existing.data "password" | b64dec }}
{{- else }}
{{- $value = randAlphaNum 48 }}
{{- end }}
{{- $_ := set .Values.valkey "_drawnPassword" $value }}
{{- end }}
{{- index .Values.valkey "_drawnPassword" }}
{{- end }}

{{/*
The checksum every pod that reads the Valkey password carries - Valkey and each
console - so that a changed password rolls all of them rather than some. Over
the Secret this chart writes, or over the name of valkey.existingSecret, which
is not the chart's to read: rotating the password inside a Secret you manage
needs a `kubectl rollout restart` of both.
*/}}
{{- define "kubetower.valkeySecretChecksum" -}}
{{- if .Values.valkey.existingSecret -}}
{{- .Values.valkey.existingSecret | sha256sum -}}
{{- else -}}
{{- include (print .Template.BasePath "/valkey-secret.yaml") . | sha256sum -}}
{{- end -}}
{{- end }}

{{/*
The console's half: where the store is and how to authenticate to it, and the
statement that this process is one of several. Included in the Deployment's
env block; renders nothing with one replica.
*/}}
{{- define "kubetower.valkeyEnv" -}}
{{- if include "kubetower.several" . }}
# One of several replicas. The console refuses to start without the store,
# and does not offer what only one pod can hold - preference writes, settings
# writes, port-forwards, the archive.
- name: KT_REPLICATED
  value: "true"
- name: KT_VALKEY_ADDR
  value: {{ printf "%s:%d" (include "kubetower.valkeyName" .) (int .Values.valkey.service.port) | quote }}
- name: KT_VALKEY_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "kubetower.valkeySecretName" . | quote }}
      key: {{ include "kubetower.valkeySecretKey" . | quote }}
{{- end }}
{{- end }}
