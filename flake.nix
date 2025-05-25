{
  description = "Thaw - Wake sleeping machines";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          flask
        ]);

        thaw = pkgs.stdenv.mkDerivation {
          pname = "thaw";
          version = "1.0.0";
          
          src = ./.;
          
          buildInputs = [ pythonEnv ];
          
          installPhase = ''
            mkdir -p $out/bin $out/share/thaw
            
            # Copy Python script and templates
            cp thaw.py $out/share/thaw/
            cp -r templates $out/share/thaw/
            
            # Create wrapper script
            cat > $out/bin/thaw << EOF
            #!${pkgs.bash}/bin/bash
            cd $out/share/thaw
            exec ${pythonEnv}/bin/python thaw.py "\$@"
            EOF
            
            chmod +x $out/bin/thaw
          '';
          
          meta = {
            description = "Wake sleeping machines via web interface";
            homepage = "https://github.com/parthiv-krishna/thaw";
            license = pkgs.lib.licenses.mit;
            maintainers = [ ];
          };
        };

      in {
        packages = {
          default = thaw;
          thaw = thaw;
        };

        apps = {
          default = {
            type = "app";
            program = "${thaw}/bin/thaw";
          };
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            pkgs.iputils  # for ping command
          ];
          
          shellHook = ''
            echo "Thaw development environment"
            echo "Run: python thaw.py --help"
          '';
        };

        # NixOS module
        nixosModules.thaw = { config, lib, pkgs, ... }:
          with lib;
          let
            cfg = config.services.thaw;
            
            # Generate machines.json from NixOS configuration
            machinesJson = pkgs.writeText "machines.json" (builtins.toJSON cfg.machines);
            
          in {
            options.services.thaw = {
              enable = mkEnableOption "Thaw wake-on-LAN service";
              
              port = mkOption {
                type = types.int;
                default = 8080;
                description = "Port for the web interface";
              };
              
              machines = mkOption {
                type = types.attrsOf (types.submodule {
                  options = {
                    ip = mkOption {
                      type = types.str;
                      description = "IP address to ping for status checks";
                    };
                    
                    mac = mkOption {
                      type = types.str;
                      description = "MAC address for wake-on-LAN";
                    };
                    
                    broadcast_ip = mkOption {
                      type = types.str;
                      description = "Broadcast IP for wake-on-LAN packets";
                    };
                    
                    timeout_seconds = mkOption {
                      type = types.int;
                      default = 1;
                      description = "Ping timeout (seconds)";
                    };
                    
                    wake_port = mkOption {
                      type = types.int;
                      default = 9;
                      description = "UDP port for wake-on-LAN";
                    };
                    
                    display_name = mkOption {
                      type = types.str;
                      default = "";
                      description = "Human-readable name (defaults to machine name)";
                    };
                  };
                });
                default = {};
                description = "Machines to monitor and wake";
              };
            };

            config = mkIf cfg.enable {
              # Set display_name defaults
              services.thaw.machines = mapAttrs (name: machine: 
                machine // {
                  display_name = if machine.display_name == "" then name else machine.display_name;
                }
              ) cfg.machines;

              systemd.services.thaw = {
                description = "Thaw wake-on-LAN service";
                wantedBy = [ "multi-user.target" ];
                after = [ "network.target" ];
                
                serviceConfig = {
                  Type = "simple";
                  User = "thaw";
                  Group = "thaw";
                  Restart = "always";
                  RestartSec = "10s";
                  
                  ExecStart = "${thaw}/bin/thaw --machines ${machinesJson} --port ${toString cfg.port}";
                  
                  # Security settings
                  NoNewPrivileges = true;
                  ProtectSystem = "strict";
                  ProtectHome = true;
                  PrivateTmp = true;
                  PrivateDevices = true;
                  ProtectHostname = true;
                  ProtectClock = true;
                  ProtectKernelTunables = true;
                  ProtectKernelModules = true;
                  ProtectKernelLogs = true;
                  ProtectControlGroups = true;
                  RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
                  RestrictNamespaces = true;
                  LockPersonality = true;
                  MemoryDenyWriteExecute = true;
                  RestrictRealtime = true;
                  RestrictSUIDSGID = true;
                  RemoveIPC = true;
                  
                  # Required capabilities for networking
                  CapabilityBoundingSet = [ "CAP_NET_RAW" ];
                  AmbientCapabilities = [ "CAP_NET_RAW" ];
                };
              };

              # Create thaw user
              users.users.thaw = {
                isSystemUser = true;
                group = "thaw";
                description = "Thaw service user";
              };
              
              users.groups.thaw = {};

              # Open firewall for the web interface (optional, can be configured separately)
              # networking.firewall.allowedTCPPorts = [ cfg.port ];
            };
          };
      }) // {
        # Export the NixOS module for other flakes
        nixosModules.default = self.nixosModules.${builtins.currentSystem}.thaw;
      };
} 