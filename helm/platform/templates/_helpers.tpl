{{/*
Full reference for one of Argus's own images: (list . "argus-anomaly-detector").
*/}}
{{- define "argus.image" -}}
{{- $root := index . 0 -}}
{{- $tag := required "image.tag is required: the git commit SHA the images were built from (deploy.sh and Argo CD set it)" $root.Values.image.tag -}}
{{- printf "%s/%s:%s" $root.Values.image.registry (index . 1) $tag -}}
{{- end }}
