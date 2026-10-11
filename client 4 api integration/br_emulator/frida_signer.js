// frida_signer.js — Shopee 앱 서명 함수 후킹/탐색 (에뮬레이터, 실기기 대체)
//
// 목적: 앱이 get_pc/item_get 요청에 붙이는 안티봇 서명(x-sap-sec, af-ac-enc-dat 등)을
//       (1) 실제 요청에서 관찰하고 (탐색), (2) 서명 함수를 rpc 로 노출해 임의 요청에
//       서명을 생성(스케일 서명)한다.
//
// 연구 근거: libshpssdk.so / com.shopee.shpssdk (yxfzedu, CSDN, xsblog iOS 대응)
// SSL unpin: br_emulator/vendor/android-certificate-unpinning.js (HTTP Toolkit OSS)
//
// 실행: .venv\Scripts\python.exe -m br_emulator.signer_bridge --spawn com.shopee.br

'use strict';

var OBSERVED = {};
var SIGN_FN = null;

// 앱 버전별 확정 후 채움. 예:
// var SIGN_SPEC = { clazz: 'com.shopee.shpssdk.wvvvuwwu', method: 'vuwuuwvw',
//                   argTypes: ['[B', '[B'] };
var SIGN_SPEC = null;

function log(tag, msg) { send({ t: tag, m: msg }); }

function hookOkHttp() {
    try {
        var Builder = Java.use('okhttp3.Request$Builder');
        var overloads = Builder.header.overloads;
        for (var i = 0; i < overloads.length; i++) {
            overloads[i].implementation = function (name, value) {
                try {
                    var n = ('' + name).toLowerCase();
                    if (n.indexOf('sap') >= 0 || n.indexOf('af-ac') >= 0 ||
                        n.indexOf('anti') >= 0 || n.indexOf('sign') >= 0 ||
                        n === 'x-sz-sdk-tongdun' || n.indexOf('x-sz') >= 0) {
                        log('sig-header', name + ' = ' + ('' + value).slice(0, 64));
                    }
                } catch (e) {}
                return this.header(name, value);
            };
        }
        log('info', 'OkHttp Request$Builder.header 훅 설치');
    } catch (e) {
        log('warn', 'OkHttp 훅 실패: ' + e);
    }
}

function hookShpssdk() {
    try {
        var Cls = Java.use('com.shopee.shpssdk.wvvvuwwu');
        if (Cls.vuwuuwvw) {
            Cls.vuwuuwvw.overload('[B', '[B').implementation = function (a, b) {
                var out = this.vuwuuwvw(a, b);
                try {
                    log('shpssdk', 'vuwuuwvw out=' + ('' + out).slice(0, 80));
                } catch (e) {}
                return out;
            };
            log('info', 'SHPSSDK vuwuuwvw hooked');
        }
    } catch (e) {
        log('warn', 'SHPSSDK hook skip: ' + e);
    }
    try {
        var SDK = Java.use('com.shopee.shpssdk.SHPSSDK');
        var methods = SDK.class.getDeclaredMethods();
        for (var i = 0; i < Math.min(methods.length, 40); i++) {
            log('candidate', 'SHPSSDK.' + methods[i].getName());
        }
    } catch (e2) {
        log('warn', 'SHPSSDK class: ' + e2);
    }
}

function scanSigners() {
    var hits = [];
    try {
        Java.enumerateLoadedClassesSync().forEach(function (cn) {
            var l = cn.toLowerCase();
            if ((l.indexOf('sap') >= 0 || l.indexOf('antifraud') >= 0 ||
                 l.indexOf('sign') >= 0 || l.indexOf('szsecurity') >= 0 ||
                 l.indexOf('shpssdk') >= 0 || l.indexOf('tongdun') >= 0) &&
                (l.indexOf('shopee') >= 0 || l.indexOf('shpssdk') >= 0)) {
                hits.push(cn);
            }
        });
    } catch (e) {}
    log('scan', '서명 후보 클래스 ' + hits.length + '개');
    hits.slice(0, 60).forEach(function (c) { log('candidate', c); });
}

function bindSigner() {
    if (!SIGN_SPEC) { return; }
    try {
        var C = Java.use(SIGN_SPEC.clazz);
        SIGN_FN = function (input) {
            var r = null;
            Java.perform(function () {
                r = C[SIGN_SPEC.method].apply(C, [input]);
            });
            return '' + r;
        };
        log('info', '서명 함수 바인딩: ' + SIGN_SPEC.clazz + '.' + SIGN_SPEC.method);
    } catch (e) {
        log('warn', '서명 함수 바인딩 실패: ' + e);
    }
}

rpc.exports = {
    sign: function (input) {
        if (!SIGN_FN) { return null; }
        try { return SIGN_FN(input); } catch (e) { return null; }
    },
    observed: function () { return OBSERVED; },
    rescan: function () { scanSigners(); hookShpssdk(); return true; }
};

Java.perform(function () {
    log('info', 'frida_signer 로드 — SHPSSDK + OkHttp 탐색');
    hookOkHttp();
    hookShpssdk();
    scanSigners();
    bindSigner();
});
