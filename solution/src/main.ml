(* survey: the tree survey report for one SR-4 run, read off the base station's archive.

   Pulls every frame the archive holds, keeps one copy of each, puts them back in the order the
   rover made them, corrects the mission computer's stamps for the clock step, walks the drive
   frames boot by boot into a centre-line odometer, and places each sharp tag on the receiver's
   track. Prints one line of JSON. *)

let gps_epoch_ms = 315_964_800_000
let week_ms = 604_800_000
let leap_ms = 18_000
let era_weeks = 2048
let epoch_ms = 200

(* floor division for a positive divisor; OCaml's own / truncates toward zero *)
let fdiv a b = if a >= 0 then a / b else - ((- a + b - 1) / b)

(* a 16-bit counter difference read the short way round *)
let s16 d = let d = d land 0xFFFF in if d >= 0x8000 then d - 0x10000 else d

(* ---- the archive ------------------------------------------------------------------------ *)

let open_archive () =
  let host = match Sys.getenv_opt "TELEMETRY_HOST" with
    | Some h when h <> "" -> h | _ -> failwith "TELEMETRY_HOST is not set" in
  let port = match Sys.getenv_opt "TELEMETRY_PORT" with
    | Some p -> int_of_string (String.trim p) | None -> failwith "TELEMETRY_PORT is not set" in
  let addr =
    try Unix.inet_addr_of_string host
    with Failure _ ->
      let h = Unix.gethostbyname host in
      if Array.length h.Unix.h_addr_list = 0 then failwith ("no address for " ^ host);
      h.Unix.h_addr_list.(0) in
  let sock = Unix.socket Unix.PF_INET Unix.SOCK_STREAM 0 in
  Unix.connect sock (Unix.ADDR_INET (addr, port));
  (Unix.in_channel_of_descr sock, Unix.out_channel_of_descr sock)

let fetch_all () =
  let ic, oc = open_archive () in
  let acc = ref [] in
  let rec ask cursor =
    output_string oc ("FROM " ^ cursor ^ " 1000\n");
    flush oc;
    let head = String.trim (input_line ic) in
    match String.split_on_char ' ' head with
    | [ "BUSY" ] ->
      Unix.sleepf 0.1;
      ask cursor
    | [ "BATCH"; k; next ] ->
      for _ = 1 to int_of_string k do acc := input_line ic :: !acc done;
      if next <> "done" then ask next
    | _ -> failwith ("the archive answered: " ^ head)
  in
  ask "start";
  (try output_string oc "QUIT\n"; flush oc with _ -> ());
  List.rev !acc

(* ---- frames ----------------------------------------------------------------------------- *)

type frame = {
  text : string;          (* the frame as the rover sent it, without the arrival time *)
  l16 : int;              (* the radio's 16-bit frame number *)
  kind : string;
  f : string array;       (* the fields after the type *)
  mutable u : int;        (* the frame number unwrapped *)
}

let parse line =
  let line = String.trim line in
  let sp = String.index line ' ' in
  let text = String.sub line (sp + 1) (String.length line - sp - 1) in
  match String.split_on_char ',' text with
  | l :: kind :: rest -> { text; l16 = int_of_string l; kind; f = Array.of_list rest; u = 0 }
  | _ -> failwith ("unreadable frame: " ^ line)

let int_f fr i = int_of_string fr.f.(i)

let () =
  let lines = fetch_all () in
  (* one copy of each frame: a copy sent again is the same bytes *)
  let seen = Hashtbl.create 200_000 in
  let kept = List.filter_map (fun ln ->
      let fr = parse ln in
      if Hashtbl.mem seen fr.text then None
      else (Hashtbl.add seen fr.text (); Some fr)) lines in
  (* unwrap the frame number along arrival order, each to the value nearest the one before *)
  let last = ref None in
  List.iter (fun fr ->
      let u = match !last with
        | None -> fr.l16
        | Some p -> p + ((fr.l16 - p + 0x8000) land 0xFFFF) - 0x8000 in
      fr.u <- u;
      last := Some u) kept;
  let frames = Array.of_list kept in
  Array.stable_sort (fun a b -> compare a.u b.u) frames;
  let n = Array.length frames in
  if n = 0 then failwith "the archive holds no frames";
  let lost = frames.(n - 1).u - frames.(0).u + 1 - n in

  (* the clock step: stamps made before it (in the rover's own order) are off by it *)
  let sys = match Array.to_list frames |> List.find_opt (fun fr -> fr.kind = "SYS") with
    | Some s -> s | None -> failwith "no SYS frame" in
  let step = int_f sys 1 in
  let true_t fr = let t = int_f fr 0 in if fr.u < sys.u then t + step else t in

  (* the controller's calibration *)
  let cfg = match Array.to_list frames |> List.find_opt (fun fr -> fr.kind = "CFG") with
    | Some c -> c | None -> failwith "no CFG frame" in
  let lines_ = int_f cfg 1 and gear = int_f cfg 2 in
  let circ_l = int_f cfg 3 and circ_r = int_f cfg 4 in
  let mirror = String.trim cfg.f.(5) in
  let cpr = 4 * lines_ * gear in
  let sl = if mirror = "left" then -1 else 1 in
  let sr = if mirror = "right" then -1 else 1 in

  (* the odometer: twice the centre's travel, in micrometres times counts per revolution *)
  let marks_t = ref [] and marks_v = ref [] in
  let acc = ref 0 in
  let prev = ref None in
  Array.iter (fun fr ->
      if fr.kind = "DRV" then begin
        let up = int_f fr 1 and cl = int_f fr 2 and cr = int_f fr 3 in
        let pl, pr = match !prev with
          | None -> (cl, cr)                              (* the run starts here *)
          | Some (pu, _, _) when up < pu -> (0, 0)        (* a new boot counts from zero *)
          | Some (_, pcl, pcr) -> (pcl, pcr) in
        let dl = s16 (cl - pl) and dr = s16 (cr - pr) in
        acc := !acc + abs (sl * dl * circ_l + sr * dr * circ_r);
        marks_t := true_t fr :: !marks_t;
        marks_v := !acc :: !marks_v;
        prev := Some (up, cl, cr)
      end) frames;
  let marks_t = Array.of_list (List.rev !marks_t) and marks_v = Array.of_list (List.rev !marks_v) in
  let div = 2 * cpr * 1000 in
  let distance = !acc / div in
  let odometer t =
    (* the last drive frame at or before t *)
    let lo = ref 0 and hi = ref (Array.length marks_t) in
    while !lo < !hi do
      let mid = (!lo + !hi) / 2 in
      if marks_t.(mid) <= t then lo := mid + 1 else hi := mid
    done;
    if !lo = 0 then 0 else marks_v.(!lo - 1) / div in

  (* the receiver's fixes, on the UTC clock *)
  let fixes = Hashtbl.create 20_000 in
  Array.iter (fun fr ->
      if fr.kind = "FIX" then begin
        let wk = int_f fr 0 and tow = int_f fr 1 in
        let t = gps_epoch_ms + (era_weeks + wk) * week_ms + tow - leap_ms in
        Hashtbl.replace fixes t (int_f fr 2, int_f fr 3, int_f fr 4)
      end) frames;
  let position t =
    let t0 = t - (t mod epoch_ms) in
    let t1 = t0 + epoch_ms in
    match Hashtbl.find_opt fixes t0, Hashtbl.find_opt fixes t1 with
    | Some (e0, n0, 4), Some (e1, n1, 4) ->
      let at a b = fdiv (a * (t1 - t) + b * (t - t0)) (t1 - t0) in
      Some (at e0 e1, at n0 n1)
    | _ -> None in

  (* the tags: each tree at its sharp tag *)
  let tags = Hashtbl.create 1000 in
  Array.iter (fun fr ->
      if fr.kind = "TAG" then begin
        let id = int_f fr 1 in
        let prev = try Hashtbl.find tags id with Not_found -> [] in
        Hashtbl.replace tags id ((true_t fr, String.trim fr.f.(2)) :: prev)
      end) frames;
  let ids = Hashtbl.fold (fun k _ a -> k :: a) tags [] |> List.sort compare in
  let b = Buffer.create 65536 in
  Buffer.add_string b (Printf.sprintf "{\"distance_mm\":%d,\"lost_frames\":%d,\"trees\":[" distance lost);
  let first = ref true in
  List.iter (fun id ->
      let attempts = List.rev (Hashtbl.find tags id) in
      match List.find_opt (fun (_, s) -> s = "ok") attempts with
      | None -> ()
      | Some (t, _) ->
        if not !first then Buffer.add_char b ',';
        first := false;
        let e, nn = match position t with
          | Some (e, nn) -> (string_of_int e, string_of_int nn)
          | None -> ("null", "null") in
        Buffer.add_string b (Printf.sprintf
          "{\"id\":%d,\"utc_ms\":%d,\"odometer_mm\":%d,\"east_mm\":%s,\"north_mm\":%s}"
          id t (odometer t) e nn)) ids;
  Buffer.add_string b "]}\n";
  print_string (Buffer.contents b)
