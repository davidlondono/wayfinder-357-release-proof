FROM --platform=$BUILDPLATFORM golang:1.26.0-bookworm AS build

ARG TARGETOS
ARG TARGETARCH
ARG VERSION=dev

WORKDIR /src
COPY go.mod ./
COPY cmd/server ./cmd/server
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath \
	-ldflags="-s -w -X main.version=$VERSION" -o /out/server ./cmd/server

FROM scratch
COPY --from=build /out/server /server
ENTRYPOINT ["/server"]
