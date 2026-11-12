/// Entry point, and the check that decides between login and chat.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'local_store.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: PayGrow.black,
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );
  runApp(const PayGrowApp());
}

class PayGrowApp extends StatelessWidget {
  const PayGrowApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'PayGrow',
      debugShowCheckedModeBanner: false,
      theme: PayGrow.theme(),
      home: const Gate(),
    );
  }
}

/// Decides between the login screen and the chat, and restores a saved token.
class Gate extends StatefulWidget {
  const Gate({super.key});

  @override
  State<Gate> createState() => _GateState();
}

class _GateState extends State<Gate> {
  bool _ready = false;
  bool _signedIn = false;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    final api = PayGrowApi.instance;
    final saved = await LocalStore.read();

    final deviceId = (saved['deviceId'] ?? '').trim();
    api.deviceId = deviceId.isNotEmpty
        ? deviceId
        : 'device-${DateTime.now().millisecondsSinceEpoch}';
    if (deviceId.isEmpty) {
      await LocalStore.write({...saved, 'deviceId': api.deviceId});
    }

    api.token = saved['token'];
    if (api.token != null) {
      final me = await api.me();
      if (me == null) api.token = null;
    }

    if (mounted) {
      setState(() {
        _ready = true;
        _signedIn = api.token != null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) {
      return const Scaffold(
        backgroundColor: PayGrow.black,
        body: Center(
          child: SizedBox(
            width: 26,
            height: 26,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: PayGrow.orange,
            ),
          ),
        ),
      );
    }
    if (!_signedIn) {
      return LoginScreen(onSignedIn: () => setState(() => _signedIn = true));
    }
    return const HomeScreen();
  }
}
