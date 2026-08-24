package main

import (
	"log"
	"os"

	"xiao-stream/server/internal/serverapp"
)

func main() {
	cfg, err := serverapp.LoadConfig(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}

	if err := serverapp.Run(cfg); err != nil {
		log.Fatal(err)
	}
}
