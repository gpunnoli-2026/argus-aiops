{{/*
Sync policy shared by every child Application. No sync waves: an app that
needs CRDs another app installs (PrometheusRule, ServiceMonitor) fails its
first sync and the retry picks it up once they exist.
*/}}
{{- define "argus.syncPolicy" -}}
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  retry:
    limit: 10
    backoff:
      duration: 15s
      factor: 2
      maxDuration: 3m
  syncOptions:
    - CreateNamespace=true
    {{- range . }}
    - {{ . }}
    {{- end }}
{{- end }}
