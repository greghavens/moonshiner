package releasecheck_test

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"
)

const assetMarker = "moonshiner-runtime-asset-v1"

func TestReleasePipeline(t *testing.T) {
	sourceRoot := projectRoot(t)
	projectRoot := filepath.Join(t.TempDir(), "project")
	copyTree(t, sourceRoot, projectRoot)
	initRepository(t, projectRoot)

	before := snapshot(t, filepath.Join(projectRoot, "internal", "assets", "generated"))
	run(t, projectRoot, nil, "go", "generate", "./internal/assets")
	after := snapshot(t, filepath.Join(projectRoot, "internal", "assets", "generated"))
	if !reflect.DeepEqual(after, before) {
		t.Fatalf("go generate changed checked-in assets:\nbefore: %v\nafter:  %v", before, after)
	}
	firstDist := filepath.Join(projectRoot, "dist-first")
	run(t, projectRoot, []string{"DIST_DIR=" + firstDist}, "sh", "scripts/build-release.sh")
	for _, goos := range []string{"linux", "windows"} {
		assertTargetEmbeds(t, projectRoot, goos, []string{
			"generated/fallback.txt",
			"generated/index.html",
			"generated/manifest.json",
		})
	}
	artifacts := []string{
		"distill-linux-amd64",
		"distill-windows-amd64.exe",
	}
	for _, artifact := range artifacts {
		data, err := os.ReadFile(filepath.Join(firstDist, artifact))
		if err != nil {
			t.Fatalf("read release artifact %s: %v", artifact, err)
		}
		if !bytes.Contains(data, []byte(assetMarker)) {
			t.Errorf("release artifact %s does not contain generated index marker %q", artifact, assetMarker)
		}
	}
	if t.Failed() {
		t.FailNow()
	}

	firstHashes := artifactHashes(t, firstDist, artifacts)
	secondDist := filepath.Join(projectRoot, "dist-second")
	run(t, projectRoot, []string{"DIST_DIR=" + secondDist}, "sh", "scripts/build-release.sh")
	secondHashes := artifactHashes(t, secondDist, artifacts)
	if !reflect.DeepEqual(firstHashes, secondHashes) {
		t.Fatalf("release builds are not reproducible:\nfirst:  %v\nsecond: %v", firstHashes, secondHashes)
	}

	source := filepath.Join(projectRoot, "web", "index.html")
	file, err := os.OpenFile(source, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatalf("open source asset: %v", err)
	}
	if _, err := file.WriteString("<!-- deliberately stale generated output -->\n"); err != nil {
		file.Close()
		t.Fatalf("modify source asset: %v", err)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close source asset: %v", err)
	}

	staleDist := filepath.Join(projectRoot, "dist-stale")
	output, err := runError(projectRoot, []string{"DIST_DIR=" + staleDist}, "sh", "scripts/build-release.sh")
	if err == nil {
		t.Fatal("release build accepted stale checked-in generated assets")
	}
	if !strings.Contains(output, "generated assets are not up to date") {
		t.Fatalf("release build failed without the clean-tree diagnostic:\n%s", output)
	}
	if _, statErr := os.Stat(staleDist); !os.IsNotExist(statErr) {
		t.Fatalf("release build wrote artifacts before the clean-tree check: stat error = %v", statErr)
	}
}

func assertTargetEmbeds(t *testing.T, directory, goos string, expected []string) {
	t.Helper()
	output := run(t, directory, []string{"CGO_ENABLED=0", "GOOS=" + goos, "GOARCH=amd64"}, "go", "list", "-json", "./internal/assets")
	var info struct {
		EmbedFiles []string
	}
	if err := json.Unmarshal([]byte(output), &info); err != nil {
		t.Fatalf("decode go list output for %s/amd64: %v\n%s", goos, err, output)
	}
	found := make(map[string]bool, len(info.EmbedFiles))
	for _, name := range info.EmbedFiles {
		found[filepath.ToSlash(name)] = true
	}
	for _, name := range expected {
		if !found[name] {
			t.Errorf("%s/amd64 embed files %v do not include %s", goos, info.EmbedFiles, name)
		}
	}
}

func projectRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate release test source")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
}

func copyTree(t *testing.T, source, destination string) {
	t.Helper()
	err := filepath.WalkDir(source, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		if rel == ".git" || rel == "dist" || strings.HasPrefix(rel, ".git"+string(filepath.Separator)) || strings.HasPrefix(rel, "dist"+string(filepath.Separator)) {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(destination, rel)
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("unexpected symlink %s", path)
		}
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode().Perm())
	})
	if err != nil {
		t.Fatalf("copy project: %v", err)
	}
}

func initRepository(t *testing.T, directory string) {
	t.Helper()
	run(t, directory, nil, "git", "init", "-q")
	run(t, directory, nil, "git", "add", ".")
	run(t, directory, nil, "git", "-c", "user.name=Moonshiner", "-c", "user.email=moonshiner@example.invalid", "commit", "-q", "-m", "seed")
}

func snapshot(t *testing.T, directory string) map[string][32]byte {
	t.Helper()
	result := make(map[string][32]byte)
	err := filepath.WalkDir(directory, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(directory, path)
		if err != nil {
			return err
		}
		result[filepath.ToSlash(rel)] = sha256.Sum256(data)
		return nil
	})
	if err != nil {
		t.Fatalf("snapshot %s: %v", directory, err)
	}
	return result
}

func artifactHashes(t *testing.T, directory string, artifacts []string) []string {
	t.Helper()
	result := make([]string, 0, len(artifacts))
	for _, artifact := range artifacts {
		data, err := os.ReadFile(filepath.Join(directory, artifact))
		if err != nil {
			t.Fatalf("read release artifact %s: %v", artifact, err)
		}
		result = append(result, fmt.Sprintf("%s:%x", artifact, sha256.Sum256(data)))
	}
	sort.Strings(result)
	return result
}

func run(t *testing.T, directory string, extraEnv []string, name string, args ...string) string {
	t.Helper()
	output, err := runError(directory, extraEnv, name, args...)
	if err != nil {
		t.Fatalf("run %s %s: %v\n%s", name, strings.Join(args, " "), err, output)
	}
	return output
}

func runError(directory string, extraEnv []string, name string, args ...string) (string, error) {
	command := exec.Command(name, args...)
	command.Dir = directory
	command.Env = append(os.Environ(), "GOTOOLCHAIN=local", "GOPROXY=off", "GOSUMDB=off")
	command.Env = append(command.Env, extraEnv...)
	output, err := command.CombinedOutput()
	return string(output), err
}
