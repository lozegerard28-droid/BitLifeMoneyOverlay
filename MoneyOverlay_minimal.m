// Minimal test dylib — fait rien au chargement
// Si l'app se lance avec ceci, le bug est dans le code ObjC/UIKit du dylib original

#import <Foundation/Foundation.h>

__attribute__((constructor))
static void init(void) {
    // Ne rien faire du tout
}
